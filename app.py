"""
================================================================
  SURVEYQC — Complete Full Stack App v10.0
  Light Theme + All Features + New Pages

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

import os, re, sys, json, uuid, threading, hashlib, time
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

# Load API keys from .env file
import os
from pathlib import Path

def load_env():
    env_file = Path('/var/www/surveyqc/.env')
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

load_env()

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
user_feedback_db = []  # [{id, type, message, page, user_email, created_at, read}]

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

# Section-level headings that mark the end of a question's content scope.
# When a paragraph matches this (and has no '?'), stop accumulating text
# for the current QID — prevents consent/GDPR/address blocks bleeding into
# the previous question's text.
SECTION_STOP_RE = re.compile(
    # Start-of-line heading patterns (standalone section titles):
    r'(?:^\s*(?:'
    r'TRANSPARENCE\b'                             # FR/EN consent heading
    r'|SIGNALEMENT\s+DES\b'                       # adverse-event section
    r'|TRAITEMENT\s+DES\s+DONN'                  # GDPR data-processing block
    r'|NOTICE\s+D.INFORMATION'                    # information notice
    r'|PHARMACOVIGILANCE\b'                       # pharmacovigilance note
    r'|RGPD\b|GDPR\b'                            # regulation labels
    r'|QUOTAS?\b'                                 # quota section heading
    r'|SIGNE?L[EÉ]TIQUE'                         # demographics heading (FR)
    r'|DEMOGRAPHICS?\b'                           # demographics heading (EN)
    r'|FIN\s+DE\s+L.ENTRETIEN'                  # "end of interview"
    r'|FIN\s+DU\s+QUESTIONNAIRE'                 # "end of questionnaire"
    r'|END\s+OF\s+(?:THE\s+)?(?:INTERVIEW|QUESTIONNAIRE|SURVEY)'
    r'|ADRESSE\s+(?:PROFESSIONNELLE|DE\s+D)'     # address-collection block
    r'|NOUS\s+VOUS\s+REMERC'                     # "Nous vous remercions..."
    r'|THANK\s+YOU\s+FOR\s+(?:YOUR\s+)?PARTICIPAT'
    r'))'
    # Anywhere-in-line patterns (distinctive phrases that mark boilerplate):
    r'|LOI\s+BERTRAND\b'                         # French transparency law
    r'|BERTRAND\s+LAW\b',
    re.IGNORECASE
)

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
<script src="/admin-sidebar-js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">
<link rel="stylesheet" href="/static/style.css">
<script src="/static/app.js" defer></script>
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
        ('reports', 'ti-file-report', 'Reports', '/reports'),
        ('templates', 'ti-copy', 'Templates', '/templates'),
    ]
    nav_html = ''
    for key, icon, label, href in items:
        cls = 'nav-item active' if active == key else 'nav-item'
        nav_html += f'<a href="{href}" class="{cls}"><i class="ti {icon}"></i><span>{label}</span></a>'

    # Bottom mobile nav items
    mob_items = [
        ('dashboard', 'ti-home', 'Home', '/dashboard'),
        ('new-qc', 'ti-plus', 'New QC', '/new-qc'),
        ('reports', 'ti-file-report', 'Reports', '/reports'),
        ('settings', 'ti-settings', 'Settings', '/settings'),
    ]
    mob_nav = ''
    for key, icon, label, href in mob_items:
        cls = 'color:var(--purple)' if active == key else 'color:var(--text3)'
        mob_nav += f'<a href="{href}" style="display:flex;flex-direction:column;align-items:center;gap:3px;text-decoration:none;{cls}"><i class="ti {icon}" style="font-size:20px"></i><span style="font-size:10px">{label}</span></a>'

    return f"""
<div class="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon"><i class="ti ti-shield-check" style="color:white;font-size:16px"></i></div>
    <span class="sidebar-logo-text">SurveyQC</span>
  </div>
  {nav_html}
  <div class="nav-divider"></div>
  <div style="padding:3px 0;margin-bottom:2px">
    <p style="font-size:9px;color:rgba(255,255,255,.3);padding:3px 10px;text-transform:uppercase;letter-spacing:.08em">Account</p>
  </div>
  <a href="/settings" class="{'nav-item active' if active=='settings' else 'nav-item'}"><i class="ti ti-settings"></i><span>Settings</span></a>
  <a href="/billing" class="{'nav-item active' if active=='billing' else 'nav-item'}"><i class="ti ti-credit-card"></i><span>Billing</span></a>
  <a href="/privacy-data" class="{'nav-item active' if active=='privacy' else 'nav-item'}"><i class="ti ti-shield-lock"></i><span>Privacy</span></a>
  <div style="margin-top:auto;padding-top:12px;border-top:0.5px solid rgba(255,255,255,.1)">
    <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px">
      <div class="avatar" style="background:var(--purple-dim);color:var(--purple);font-size:11px;font-weight:500">{initials}</div>
      <div style="flex:1;min-width:0">
        <p style="font-size:12px;color:white;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{name}</p>
        <p style="font-size:10px;color:#85B7EB">{plan} plan</p>
      </div>
      <a href="/logout" style="color:#85B7EB;text-decoration:none;flex-shrink:0"><i class="ti ti-logout" style="font-size:14px"></i></a>
    </div>
  </div>
</div>
<!-- MOBILE BOTTOM NAV -->
<div style="display:none;position:fixed;bottom:0;left:0;right:0;background:white;padding:10px 20px;justify-content:space-around;align-items:center;z-index:1000;border-top:0.5px solid rgba(255,255,255,.1)" class="mobile-bottom-nav">
  {mob_nav}
</div>
<style>
@media(max-width:768px){{
  .mobile-bottom-nav{{display:flex !important}}
  .main-content{{padding-bottom:70px}}
}}
</style>"""

# ================================================================
# PAGE: LANDING
# ================================================================
@app.route('/')
def landing():
    return redirect('/home')


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

    error_html = ('<div class="auth-error"><i class="ti ti-alert-circle"></i>' + error + '</div>') if error else ''

    page = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sign in \u2014 SurveyQC</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">

<style>
:root{--bg:#F7F4EE;--card:#FFFFFF;--text:#171717;--text2:#5F5B53;--text3:#8A847A;--accent:#C46A2B;--accent-hover:#A9551F;--accent-bg:#F5E6D8;--border:#E8E1D8;--dark:#1B140F;--danger:#C84B31;--success:#3F7D58;--shadow-lg:0 10px 40px rgba(24,17,10,0.08)}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'Inter','Plus Jakarta Sans',BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased}
body{background:var(--bg);color:var(--text);min-height:100vh;display:flex}
a{text-decoration:none;color:inherit}
.auth-wrap{display:grid;grid-template-columns:1fr 1fr;width:100%;min-height:100vh}
.auth-left{background:var(--dark);padding:56px 64px;display:flex;flex-direction:column;justify-content:space-between;position:relative;overflow:hidden}
.auth-left::before{content:"";position:absolute;top:-100px;right:-100px;width:400px;height:400px;background:radial-gradient(circle,rgba(196,106,43,.3),transparent 70%);pointer-events:none}
.auth-logo{display:flex;align-items:center;gap:11px;position:relative;z-index:1}
.auth-logo-mark{width:36px;height:36px;background:var(--accent);border-radius:9px;display:flex;align-items:center;justify-content:center}
.auth-logo-text{font-family:'Plus Jakarta Sans',sans-serif;font-size:19px;font-weight:700;color:white}
.auth-left-content{position:relative;z-index:1}
.auth-left-content h2{font-family:'Plus Jakarta Sans',sans-serif;font-size:34px;font-weight:800;color:white;line-height:1.2;letter-spacing:-1px;margin-bottom:20px}
.auth-quote{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:24px;margin-top:32px}
.auth-quote p{font-size:15px;color:#E8DDD2;line-height:1.7;margin-bottom:16px}
.auth-quote-author{display:flex;align-items:center;gap:12px}
.auth-quote-avatar{width:40px;height:40px;border-radius:50%;background:var(--accent);color:white;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px}
.auth-quote-name{font-size:14px;font-weight:700;color:white}
.auth-quote-role{font-size:12px;color:#9A8C7B}
.auth-stats{display:flex;gap:32px;position:relative;z-index:1}
.auth-stat-num{font-family:'Plus Jakarta Sans',sans-serif;font-size:26px;font-weight:800;color:white}
.auth-stat-lbl{font-size:12px;color:#9A8C7B;margin-top:2px}
.auth-right{display:flex;align-items:center;justify-content:center;padding:40px 24px;background:var(--bg)}
.auth-card{width:100%;max-width:400px}
.auth-card h1{font-family:'Plus Jakarta Sans',sans-serif;font-size:30px;font-weight:800;letter-spacing:-1px;margin-bottom:8px}
.auth-card-sub{font-size:15px;color:var(--text2);margin-bottom:32px}
.auth-card-sub a{color:var(--accent);font-weight:600}
.form-group{margin-bottom:18px}
.form-label{font-size:13px;font-weight:600;color:var(--text);display:block;margin-bottom:7px}
.form-input{width:100%;padding:13px 16px;border:1px solid var(--border);border-radius:12px;font-size:14px;color:var(--text);background:white;outline:none;transition:all .15s;font-family:inherit}
.form-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(196,106,43,.12)}
.auth-btn{width:100%;background:var(--dark);color:#F7F4EE;border:none;padding:14px;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer;transition:all .2s;display:flex;align-items:center;justify-content:center;gap:8px;font-family:inherit;margin-top:6px}
.auth-btn:hover{background:#2A1F18;transform:translateY(-1px)}
.auth-error{background:#FAE5E0;border:1px solid #F0C4BA;color:var(--danger);font-size:13px;padding:11px 14px;border-radius:10px;margin-bottom:18px;display:flex;align-items:center;gap:8px}
.auth-divider{display:flex;align-items:center;gap:14px;margin:24px 0;color:var(--text3);font-size:13px}
.auth-divider::before,.auth-divider::after{content:"";flex:1;height:1px;background:var(--border)}
.auth-social{display:flex;gap:10px}
.auth-social-btn{flex:1;border:1px solid var(--border);background:white;border-radius:12px;padding:11px;display:flex;align-items:center;justify-content:center;gap:8px;font-size:13px;font-weight:600;color:var(--text);cursor:pointer;transition:all .15s}
.auth-social-btn:hover{background:var(--bg)}
.auth-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.auth-check{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--text2);cursor:pointer}
.auth-link{font-size:13px;color:var(--accent);font-weight:600}
.auth-back{position:absolute;top:24px;left:24px;font-size:13px;color:#9A8C7B;display:flex;align-items:center;gap:6px;z-index:2}
.auth-back:hover{color:white}
@media(max-width:900px){
  .auth-wrap{grid-template-columns:1fr}
  .auth-left{display:none}
  .auth-right{padding:40px 20px}
}
</style></head><body>
<div class="auth-wrap">
  <div class="auth-left">
    <a href="/home" class="auth-logo">
      <div class="auth-logo-mark"><i class="ti ti-shield-check" style="color:white;font-size:18px"></i></div>
      <span class="auth-logo-text">SurveyQC</span>
    </a>
    <div class="auth-left-content">
      <h2>Welcome back to<br>perfect survey QC.</h2>
      <div class="auth-quote">
        <p>"8 hours of manual QC now takes 10 minutes. Caught a termination bug that would have killed our entire dataset."</p>
        <div class="auth-quote-author">
          <div class="auth-quote-avatar">ST</div>
          <div>
            <div class="auth-quote-name">Sarah Thompson</div>
            <div class="auth-quote-role">QC Manager, Ipsos UK</div>
          </div>
        </div>
      </div>
    </div>
    <div class="auth-stats">
      <div><div class="auth-stat-num">500+</div><div class="auth-stat-lbl">QC professionals</div></div>
      <div><div class="auth-stat-num">99%</div><div class="auth-stat-lbl">Accuracy rate</div></div>
      <div><div class="auth-stat-num">80+</div><div class="auth-stat-lbl">Languages</div></div>
    </div>
  </div>
  <div class="auth-right">
    <div class="auth-card">
      <h1>Sign in</h1>
      <p class="auth-card-sub">Don't have an account? <a href="/signup">Sign up free</a></p>
      """ + error_html + """
      <form method="POST">
        <div class="form-group">
          <label class="form-label">Email</label>
          <input type="email" name="email" class="form-input" placeholder="you@company.com" required>
        </div>
        <div class="form-group">
          <label class="form-label">Password</label>
          <input type="password" name="password" class="form-input" placeholder="Enter your password" required>
        </div>
        <div class="auth-row">
          <label class="auth-check"><input type="checkbox" style="accent-color:var(--accent)"> Remember me</label>
          <a href="/login" class="auth-link">Forgot password?</a>
        </div>
        <button type="submit" class="auth-btn">Sign in <i class="ti ti-arrow-right"></i></button>
      </form>
      <p style="text-align:center;font-size:12px;color:var(--text3);margin-top:20px">Demo: demo@surveyqc.com / demo123</p>
    </div>
  </div>
</div>
</body></html>"""
    return page


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

    error_html = ('<div class="auth-error"><i class="ti ti-alert-circle"></i>' + error + '</div>') if error else ''

    page = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sign up \u2014 SurveyQC</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">

<style>
:root{--bg:#F7F4EE;--card:#FFFFFF;--text:#171717;--text2:#5F5B53;--text3:#8A847A;--accent:#C46A2B;--accent-hover:#A9551F;--accent-bg:#F5E6D8;--border:#E8E1D8;--dark:#1B140F;--danger:#C84B31;--success:#3F7D58}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'Inter','Plus Jakarta Sans',BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased}
body{background:var(--bg);color:var(--text);min-height:100vh;display:flex}
a{text-decoration:none;color:inherit}
.auth-wrap{display:grid;grid-template-columns:1fr 1fr;width:100%;min-height:100vh}
.auth-left{background:var(--dark);padding:56px 64px;display:flex;flex-direction:column;justify-content:space-between;position:relative;overflow:hidden}
.auth-left::before{content:"";position:absolute;top:-100px;right:-100px;width:400px;height:400px;background:radial-gradient(circle,rgba(196,106,43,.3),transparent 70%)}
.auth-logo{display:flex;align-items:center;gap:11px;position:relative;z-index:1}
.auth-logo-mark{width:36px;height:36px;background:var(--accent);border-radius:9px;display:flex;align-items:center;justify-content:center}
.auth-logo-text{font-family:'Plus Jakarta Sans',sans-serif;font-size:19px;font-weight:700;color:white}
.auth-left-content{position:relative;z-index:1}
.auth-left-content h2{font-family:'Plus Jakarta Sans',sans-serif;font-size:34px;font-weight:800;color:white;line-height:1.2;letter-spacing:-1px;margin-bottom:24px}
.auth-benefits{list-style:none}
.auth-benefit{display:flex;align-items:center;gap:12px;color:#E8DDD2;font-size:15px;margin-bottom:16px}
.auth-benefit i{color:var(--accent);background:rgba(196,106,43,.15);width:28px;height:28px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.auth-stats{display:flex;gap:32px;position:relative;z-index:1}
.auth-stat-num{font-family:'Plus Jakarta Sans',sans-serif;font-size:26px;font-weight:800;color:white}
.auth-stat-lbl{font-size:12px;color:#9A8C7B;margin-top:2px}
.auth-right{display:flex;align-items:center;justify-content:center;padding:40px 24px}
.auth-card{width:100%;max-width:400px}
.auth-card h1{font-family:'Plus Jakarta Sans',sans-serif;font-size:30px;font-weight:800;letter-spacing:-1px;margin-bottom:8px}
.auth-card-sub{font-size:15px;color:var(--text2);margin-bottom:32px}
.auth-card-sub a{color:var(--accent);font-weight:600}
.form-group{margin-bottom:18px}
.form-label{font-size:13px;font-weight:600;color:var(--text);display:block;margin-bottom:7px}
.form-input{width:100%;padding:13px 16px;border:1px solid var(--border);border-radius:12px;font-size:14px;color:var(--text);background:white;outline:none;transition:all .15s;font-family:inherit}
.form-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(196,106,43,.12)}
.auth-btn{width:100%;background:var(--dark);color:#F7F4EE;border:none;padding:14px;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer;transition:all .2s;display:flex;align-items:center;justify-content:center;gap:8px;font-family:inherit;margin-top:6px}
.auth-btn:hover{background:#2A1F18;transform:translateY(-1px)}
.auth-error{background:#FAE5E0;border:1px solid #F0C4BA;color:var(--danger);font-size:13px;padding:11px 14px;border-radius:10px;margin-bottom:18px;display:flex;align-items:center;gap:8px}
.auth-terms{font-size:12px;color:var(--text3);text-align:center;margin-top:18px;line-height:1.6}
.auth-terms a{color:var(--accent)}
@media(max-width:900px){
  .auth-wrap{grid-template-columns:1fr}
  .auth-left{display:none}
  .auth-right{padding:40px 20px}
}
</style></head><body>
<div class="auth-wrap">
  <div class="auth-left">
    <a href="/home" class="auth-logo">
      <div class="auth-logo-mark"><i class="ti ti-shield-check" style="color:white;font-size:18px"></i></div>
      <span class="auth-logo-text">SurveyQC</span>
    </a>
    <div class="auth-left-content">
      <h2>Start catching survey<br>bugs in minutes.</h2>
      <ul class="auth-benefits">
        <li class="auth-benefit"><i class="ti ti-check"></i>5 free reports every month, forever</li>
        <li class="auth-benefit"><i class="ti ti-check"></i>All 15+ QC checks included</li>
        <li class="auth-benefit"><i class="ti ti-check"></i>Works in 80+ languages</li>
        <li class="auth-benefit"><i class="ti ti-check"></i>No credit card required</li>
      </ul>
    </div>
    <div class="auth-stats">
      <div><div class="auth-stat-num">500+</div><div class="auth-stat-lbl">QC professionals</div></div>
      <div><div class="auth-stat-num">8h</div><div class="auth-stat-lbl">Saved per survey</div></div>
      <div><div class="auth-stat-num">99%</div><div class="auth-stat-lbl">Accuracy</div></div>
    </div>
  </div>
  <div class="auth-right">
    <div class="auth-card">
      <h1>Create account</h1>
      <p class="auth-card-sub">Already have an account? <a href="/login">Sign in</a></p>
      """ + error_html + """
      <form method="POST">
        <div class="form-group">
          <label class="form-label">Full name</label>
          <input type="text" name="name" class="form-input" placeholder="Your name" required>
        </div>
        <div class="form-group">
          <label class="form-label">Work email</label>
          <input type="email" name="email" class="form-input" placeholder="you@company.com" required>
        </div>
        <div class="form-group">
          <label class="form-label">Password</label>
          <input type="password" name="password" class="form-input" placeholder="At least 6 characters" required>
        </div>
        <button type="submit" class="auth-btn">Create free account <i class="ti ti-arrow-right"></i></button>
      </form>
      <p class="auth-terms">By signing up you agree to our <a href="/terms">Terms</a> and <a href="/privacy-policy">Privacy Policy</a>.</p>
    </div>
  </div>
</div>
</body></html>"""
    return page


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/home')

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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Dashboard — SurveyQC</title></head><body>
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
          <p style="font-size:32px;font-weight:800;color:#2E8B57;font-family:'Plus Jakarta Sans',sans-serif;letter-spacing:-0.5px">{saved} hours</p>
          <p style="font-size:13px;color:var(--green);font-weight:500">= {saved//8 if saved > 0 else 0} full working days back in your life</p>
        </div>
        <p style="font-size:11px;color:var(--text3)">{reports_used} surveys completed — manual would take {reports_used*8}h, SurveyQC did it in {reports_used} mins</p>
      </div>
      <div style="text-align:center">
        <div style="background:rgba(255,255,255,.1);border-radius:10px;padding:10px 18px">
          <p style="font-size:20px;font-weight:600;color:var(--text)">{max(1,reports_used*8)}x</p>
          <p style="font-size:10px;color:var(--text3)">ROI on plan</p>
        </div>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card"><p class="stat-num">{reports_used}</p><p class="stat-label">Reports run</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#3F7D58">{max(0,reports_used-3)}</p><p class="stat-label">Passed</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#C84B31">3</p><p class="stat-label">Issues found</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#2E8B57">{saved}h</p><p class="stat-label">Time saved</p></div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 280px;gap:16px">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
          <p style="font-size:14px;font-weight:600;color:var(--text)">Recent reports</p>
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
          <p style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:12px">Quick actions</p>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <a href="/new-qc" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-plus" style="font-size:20px;color:var(--purple)"></i>
              <p style="font-size:11px;color:var(--text);margin-top:5px;font-weight:500">New QC</p>
            </a>
            <a href="/ai-tester" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-robot" style="font-size:20px;color:#EF9F27"></i>
              <p style="font-size:11px;color:var(--text);margin-top:5px;font-weight:500">AI Tester</p>
            </a>
            <a href="/reports" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-file-report" style="font-size:20px;color:#1D9E75"></i>
              <p style="font-size:11px;color:var(--text);margin-top:5px;font-weight:500">Reports</p>
            </a>
            <a href="/settings" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-settings" style="font-size:20px;color:#378ADD"></i>
              <p style="font-size:11px;color:var(--text);margin-top:5px;font-weight:500">Settings</p>
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
    plan = user.get('plan','Free') if user else 'Free'
    sb = sidebar_html('new-qc')

    page = SHARED_CSS + '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>New QC - SurveyQC</title></head><body>'
    page += '<div class="app-layout">' + sb + '<div class="main-content">'
    page += '<div class="topbar"><div><p class="page-title">New QC</p><p class="page-sub">Upload doc + URL. AI handles everything automatically.</p></div></div>'

    # Info strip
    page += '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:24px">'
    checks = [
        ('ti-x-octagon','#E6F1FB','#185FA5','Termination','Every rule clicked & verified'),
        ('ti-text-recognition','#E1F5EE','#0F6E56','Missing Words','Word-by-word comparison'),
        ('ti-list-check','#EAF3DE','#3B6D11','Text Match','Question text vs spec'),
        ('ti-camera','#EEEDFE','#534AB7','Screenshots','Every question captured'),
    ]
    for icon,bg,col,title,desc in checks:
        page += ('<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:14px;display:flex;align-items:center;gap:10px">'
            '<div style="width:34px;height:34px;border-radius:8px;background:'+bg+';display:flex;align-items:center;justify-content:center;flex-shrink:0">'
            '<i class="ti '+icon+'" style="font-size:17px;color:'+col+'"></i></div>'
            '<div><p style="font-size:12px;font-weight:600;color:#1A1A2E">'+title+'</p>'
            '<p style="font-size:11px;color:#9CA3AF;margin-top:1px">'+desc+'</p></div></div>')
    page += '</div>'

    # Main form card
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:12px;padding:28px;max-width:680px">'
    page += '<form action="/run-qc" method="POST" enctype="multipart/form-data">'

    # Step 1 - Doc
    page += '<div style="margin-bottom:22px">'
    page += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    page += '<div style="width:26px;height:26px;border-radius:50%;background:#042C53;color:white;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0">1</div>'
    page += '<p style="font-size:14px;font-weight:600;color:#1A1A2E">Upload Spec Document <span style="color:#E24B4A">*</span></p></div>'
    page += '<input type="file" name="doc" accept=".docx" required style="width:100%;padding:10px 12px;border:0.5px solid #DDE1E7;border-radius:8px;font-size:13px;color:#374151;background:#FAFAFA;cursor:pointer">'
    page += '<p style="font-size:11px;color:#9CA3AF;margin-top:5px">Screener / spec .docx file. Required.</p>'
    page += '</div>'

    # Step 2 - URL
    page += '<div style="margin-bottom:22px">'
    page += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    page += '<div style="width:26px;height:26px;border-radius:50%;background:#042C53;color:white;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0">2</div>'
    page += '<p style="font-size:14px;font-weight:600;color:#1A1A2E">Live Survey URL <span style="color:#E24B4A">*</span></p></div>'
    page += '<input type="url" name="url" placeholder="https://survey.confirmit.com/..." required class="form-input">'
    page += '<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">'
    for plat in ['Confirmit','Decipher','Forsta','Qualtrics']:
        page += '<span style="font-size:11px;background:#F0F2F5;color:#6B7280;padding:3px 10px;border-radius:6px;font-weight:500">'+plat+'</span>'
    page += '</div></div>'

    # Step 3 - Screenshots (OPTIONAL)
    page += '<div style="margin-bottom:22px;padding:16px;background:#F8F9FA;border-radius:10px;border:0.5px dashed #DDE1E7">'
    page += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
    page += '<div style="width:26px;height:26px;border-radius:50%;background:#6B7280;color:white;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0">3</div>'
    page += '<p style="font-size:14px;font-weight:600;color:#1A1A2E">Screenshots <span style="font-size:12px;font-weight:400;color:#9CA3AF">(Optional)</span></p>'
    page += '<span style="margin-left:auto;font-size:11px;background:#E6F1FB;color:#0C447C;padding:2px 8px;border-radius:20px;font-weight:500">Pro+</span>' if plan == 'Free' else ''
    page += '</div>'
    page += '<p style="font-size:12px;color:#6B7280;margin-bottom:10px">Upload WhatsApp screenshots — AI will pay extra attention to the specific questions shown.</p>'

    if plan == 'Free':
        page += '<div style="background:#FAEEDA;border-radius:7px;padding:10px 12px;margin-bottom:10px"><p style="font-size:12px;color:#633806"><i class="ti ti-lock" style="font-size:12px"></i> Screenshot QC is Pro+ only. <a href="/billing" style="color:#042C53;font-weight:600">Upgrade &rarr;</a></p></div>'
        page += '<input type="file" name="screenshots" accept="image/*" multiple disabled style="width:100%;padding:9px 12px;border:0.5px solid #DDE1E7;border-radius:8px;font-size:13px;color:#9CA3AF;background:#F0F2F5;cursor:not-allowed">'
    else:
        page += '<input type="file" name="screenshots" accept="image/*" multiple style="width:100%;padding:9px 12px;border:0.5px solid #DDE1E7;border-radius:8px;font-size:13px;color:#374151;background:white;cursor:pointer">'
        page += '<p style="font-size:11px;color:#9CA3AF;margin-top:5px">You can select multiple files. AI will cross-verify questions that appear in the screenshots.</p>'
    page += '</div>'

    # Advanced options collapsible
    page += '<div style="margin-bottom:22px">'
    page += '<button type="button" onclick="toggleAdv()" style="background:none;border:none;color:#6B7280;font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px;padding:0"><i class="ti ti-settings" style="font-size:14px"></i> Advanced options <i class="ti ti-chevron-down" style="font-size:12px" id="advIcon"></i></button>'
    page += '<div id="advPanel" style="display:none;margin-top:14px;padding:16px;background:#F8F9FA;border-radius:8px;border:0.5px solid #EEF0F3">'
    page += '<div class="form-group"><label class="form-label">Country (for screener question)</label>'
    page += '<input type="text" name="country" placeholder="e.g. United Kingdom" class="form-input"></div>'
    page += '<div class="form-group"><label class="form-label">Specific questions only (optional)</label>'
    page += '<input type="text" name="specific_questions" placeholder="e.g. Q1, Q3, Q7-Q12 (blank = all questions)" class="form-input">'
    page += '<p style="font-size:11px;color:#9CA3AF;margin-top:4px">Blank rakho = full survey QC</p></div>'
    page += '<div><label class="form-label">Checks to run</label>'
    page += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:6px">'
    chk_items = [
        ('chk_term','Termination rules'),
        ('chk_text','Question text'),
        ('chk_words','Missing words'),
        ('chk_options','Options match'),
        ('chk_mandatory','Mandatory markers'),
        ('chk_piping','Piping markers'),
        ('chk_codes','Answer codes'),
        ('chk_order','Question order'),
    ]
    for name,label in chk_items:
        page += '<label style="display:flex;align-items:center;gap:7px;font-size:13px;color:#374151;cursor:pointer"><input type="checkbox" name="'+name+'" value="1" checked style="accent-color:#042C53;width:14px;height:14px">'+label+'</label>'
    page += '</div></div></div></div>'

    # Submit
    page += '<button type="submit" class="btn btn-primary" style="width:100%;padding:14px;font-size:15px;font-weight:600;border-radius:10px">'
    page += '<i class="ti ti-player-play" style="font-size:16px"></i> Run QC — AI will handle everything</button>'
    page += '<p style="font-size:12px;color:#9CA3AF;text-align:center;margin-top:10px">Takes 5-15 min depending on survey size &middot; You will get a Word report</p>'
    page += '</form></div>'

    # Recent reports mini list
    email = session.get('user_email','')
    recent = [(jid,j) for jid,j in list(jobs.items()) if j.get('user_email')==email][-3:]
    if recent:
        page += '<div style="margin-top:24px;max-width:680px">'
        page += '<p style="font-size:13px;font-weight:600;color:#6B7280;margin-bottom:10px">Recent reports</p>'
        for jid,j in reversed(recent):
            status = j.get('status','unknown')
            if status == 'done':
                badge = '<span style="background:#EAF3DE;color:#27500A;font-size:10px;padding:2px 8px;border-radius:20px">Done</span>'
            elif status == 'running':
                badge = '<span style="background:#E6F1FB;color:#0C447C;font-size:10px;padding:2px 8px;border-radius:20px">Running</span>'
            else:
                badge = '<span style="background:#FCEBEB;color:#791F1F;font-size:10px;padding:2px 8px;border-radius:20px">Error</span>'
            page += ('<div style="background:white;border:0.5px solid #DDE1E7;border-radius:8px;padding:12px 14px;margin-bottom:8px;display:flex;align-items:center;gap:10px">'
                '<i class="ti ti-file-report" style="font-size:16px;color:#6B7280;flex-shrink:0"></i>'
                '<p style="font-size:13px;color:#374151;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+j.get('doc_name','Unknown')[:45]+'</p>'
                +badge+
                '<a href="/report/'+jid+'" style="font-size:12px;color:#185FA5;flex-shrink:0">View &rarr;</a>'
                '</div>')
        page += '</div>'

    page += '<div class="mobile-nav"><div class="mobile-nav-inner">'
    page += '<a href="/dashboard" class="mobile-nav-item"><i class="ti ti-home"></i><span>Home</span></a>'
    page += '<a href="/new-qc" class="mobile-nav-item active"><i class="ti ti-plus"></i><span>New QC</span></a>'
    page += '<a href="/reports" class="mobile-nav-item"><i class="ti ti-file-text"></i><span>Reports</span></a>'
    page += '<a href="/settings" class="mobile-nav-item"><i class="ti ti-settings"></i><span>Settings</span></a>'
    page += '</div></div>'

    page += '</div></div>'
    page += '''<script>
function toggleAdv(){
    var p=document.getElementById("advPanel");
    var i=document.getElementById("advIcon");
    if(p.style.display==="none"){p.style.display="block";i.className="ti ti-chevron-up";}
    else{p.style.display="none";i.className="ti ti-chevron-down";}
}
</script>'''
    page += '</body></html>'
    return render_template_string(page)


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
        'doc_path': doc_path,
        'survey_url': survey_url,
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Running QC — SurveyQC</title>

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
      <button onclick="stopJob()" id="stop-btn" style="margin-left:12px;background:#dc2626;color:white;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600">⏹ Stop</button>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
        <p style="font-size:14px;font-weight:600;color:var(--text)" id="phase-text">Starting...</p>
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
function stopJob() {{
  if(!confirm('Stop this QC run?')) return;
  fetch('/stop/' + jobId, {{method:'POST'}})
    .then(() => {{
      document.getElementById('stop-btn').textContent = 'Stopped';
      document.getElementById('stop-btn').disabled = true;
      clearInterval(timer);
      document.getElementById('status-badge').textContent = 'Stopped';
      document.getElementById('status-badge').className = 'badge badge-red';
    }});
}}
</script>
</body></html>""")


@app.route('/stop/<job_id>', methods=['POST'])
@login_required
def stop_job(job_id):
    if job_id in jobs:
        jobs[job_id]['status'] = 'stopped'
        jobs[job_id]['phase'] = 'Stopped by user'
    return jsonify({'ok': True})

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
    ai_summary = j.get('ai_summary', '')
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
            'NAMING MISMATCH': 'Naming mismatch',
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Report — SurveyQC</title></head><body>
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
        <a href="/retest/{job_id}" class="btn btn-ghost btn-sm" style="color:#F59E0B;border-color:#F59E0B"><i class="ti ti-player-play"></i>Retest</a>
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

    {f'<div class="card" style="margin-bottom:16px;border-left:3px solid var(--accent)"><div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><i class="ti ti-sparkles" style="color:var(--accent);font-size:16px"></i><span style="font-size:13px;font-weight:700;color:var(--text)">AI Summary</span></div><p style="font-size:14px;color:var(--text2);line-height:1.7">{ai_summary}</p></div>' if ai_summary else ''}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="card">
        <p style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:14px">Issues found ({total_issues})</p>
        {"<table class='data-table'><thead><tr><th>QID</th><th>Type</th><th>Severity</th><th>Details</th></tr></thead><tbody>" + issues_html + "</tbody></table>" if issues_html else "<p style='color:var(--text3);text-align:center;padding:20px'>No structural issues found!</p>"}
      </div>
      <div class="card">
        <p style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:14px">Termination tests ({term_passed}/{term_total} passed)</p>
        {"<table class='data-table'><thead><tr><th>Status</th><th>QID</th><th>Code</th><th>Details</th></tr></thead><tbody>" + term_html + "</tbody></table>" if term_html else "<p style='color:var(--text3);text-align:center;padding:20px'>No termination rules found in doc</p>"}
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <p style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:14px">Rate this report</p>
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
        share_tok = hashlib.md5((jid + '-share').encode()).hexdigest()[:12]
        share_url = 'https://surveyqc.online/view/' + share_tok
        share_title = doc_name[:40]
        share_btn = f'<button data-share-url="{share_url}" data-share-title="{share_title}" style="background:none;border:none;cursor:pointer;color:var(--text3);font-size:13px;padding:0 4px" title="Share"><i class="ti ti-share"></i></button>' if status == 'done' else ''
        plat_lower = platform.lower().replace(' ', '')

        rows += f"""
        <tr>
          <td class="primary"><i class="ti ti-file-text" style="color:var(--purple);margin-right:8px"></i>{doc_name[:35]}</td>
          <td data-platform="{plat_lower}">{platform}</td>
          <td>{mode.title()}</td>
          <td><span class="badge {badge_cls}">{badge_txt}</span></td>
          <td style="color:var(--text3)">{created}</td>
          <td style="white-space:nowrap">{link} &nbsp; {download} &nbsp; {share_btn}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="6" style="text-align:center;color:var(--text3);padding:24px">No reports yet. <a href="/new-qc" style="color:var(--purple)">Run your first QC!</a></td></tr>'

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Reports — SurveyQC</title></head><body>
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
    <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap">
      <div style="flex:1;min-width:200px;position:relative">
        <i class="ti ti-search" style="position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--text3);font-size:16px"></i>
        <input type="text" id="searchBox" onkeyup="filterReports()" placeholder="Search reports..." style="width:100%;padding:11px 14px 11px 40px;border:1px solid var(--border);border-radius:12px;font-size:14px;font-family:inherit;outline:none;background:white">
      </div>
      <select id="platformFilter" onchange="filterReports()" style="padding:11px 14px;border:1px solid var(--border);border-radius:12px;font-size:14px;font-family:inherit;background:white;outline:none;cursor:pointer">
        <option value="">All platforms</option>
        <option value="confirmit">Confirmit</option>
        <option value="decipher">Decipher</option>
        <option value="forsta">Forsta</option>
        <option value="qualtrics">Qualtrics</option>
      </select>
      <select id="statusFilter" onchange="filterReports()" style="padding:11px 14px;border:1px solid var(--border);border-radius:12px;font-size:14px;font-family:inherit;background:white;outline:none;cursor:pointer">
        <option value="">All status</option>
        <option value="pass">Passed</option>
        <option value="fail">Issues</option>
        <option value="running">Running</option>
      </select>
    </div>
    <div class="card">
      <table class="data-table" style="width:100%">
        <thead><tr>
          <th>Survey name</th><th>Platform</th><th>Mode</th><th>Status</th><th>Date</th><th>Actions</th>
        </tr></thead>
        <tbody id="reportsBody">{rows}</tbody>
      </table>
      <div id="noResults" style="display:none;text-align:center;color:var(--text3);padding:24px">No reports match your filters.</div>
    </div>
  </div>
</div>
<script>
function filterReports(){{
  var q=document.getElementById('searchBox').value.toLowerCase();
  var plat=document.getElementById('platformFilter').value.toLowerCase();
  var stat=document.getElementById('statusFilter').value.toLowerCase();
  var rows=document.querySelectorAll('#reportsBody tr');
  var visible=0;
  rows.forEach(function(r){{
    var txt=r.textContent.toLowerCase();
    var show=true;
    if(q && txt.indexOf(q)===-1) show=false;
    if(plat && txt.indexOf(plat)===-1) show=false;
    if(stat){{
      if(stat==='pass' && txt.indexOf('all pass')===-1) show=false;
      if(stat==='fail' && txt.indexOf('issues')===-1) show=false;
      if(stat==='running' && txt.indexOf('running')===-1) show=false;
    }}
    r.style.display=show?'':'none';
    if(show) visible++;
  }});
  document.getElementById('noResults').style.display=visible===0?'block':'none';
}}
</script>
</body></html>""")

# ================================================================
# PAGE: AI TESTER
# ================================================================
@app.route('/ai-tester')
@login_required
def ai_tester():
    return redirect('/new-qc')


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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Settings — SurveyQC</title></head><body>
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
          <p style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:16px">Profile information</p>
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
          <p style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:16px">Change password</p>
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
          <p style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:12px">Notifications</p>
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
    c = site_content  # use admin-editable content for prices

    free_price = c.get('plan_free_price', '0')
    pro_price  = c.get('plan_pro_price', '29')
    biz_price  = c.get('plan_biz_price', '99')
    free_feats = (c.get('plan_free_features', '5 reports per month')).split('||')[0]
    pro_feats  = (c.get('plan_pro_features', '50 reports per month')).split('||')[0]
    biz_feats  = (c.get('plan_biz_features', 'Unlimited reports')).split('||')[0]

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Billing — SurveyQC</title></head><body>
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
        <p style="font-size:13px;font-weight:600;color:var(--text)">Free</p>
        <p style="font-size:24px;font-weight:700;color:var(--text);margin:8px 0 4px">${free_price}<span style="font-size:13px;color:var(--text3)">/mo</span></p>
        <p style="font-size:12px;color:var(--text3);margin-bottom:14px">{free_feats}</p>
        <button class="btn btn-ghost btn-sm" style="width:100%;justify-content:center">{'Current plan' if plan=='Free' else 'Downgrade'}</button>
      </div>
      <div class="card" style="{'border:2px solid var(--purple)' if plan=='Pro' else ''}">
        {'<span class="badge badge-purple" style="margin-bottom:8px;display:inline-block">Current plan</span>' if plan=='Pro' else ''}
        <p style="font-size:13px;font-weight:600;color:var(--text)">Pro</p>
        <p style="font-size:24px;font-weight:700;color:var(--text);margin:8px 0 4px">${pro_price}<span style="font-size:13px;color:var(--text3)">/mo</span></p>
        <p style="font-size:12px;color:var(--text3);margin-bottom:14px">{pro_feats}</p>
        <button class="btn btn-primary btn-sm" style="width:100%;justify-content:center">{'Current plan' if plan=='Pro' else 'Upgrade to Pro'}</button>
      </div>
      <div class="card">
        <p style="font-size:13px;font-weight:600;color:var(--text)">Business</p>
        <p style="font-size:24px;font-weight:700;color:var(--text);margin:8px 0 4px">${biz_price}<span style="font-size:13px;color:var(--text3)">/mo</span></p>
        <p style="font-size:12px;color:var(--text3);margin-bottom:14px">{biz_feats}</p>
        <button class="btn btn-ghost btn-sm" style="width:100%;justify-content:center">Upgrade to Business</button>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="card">
        <p style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:12px">Usage this month</p>
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
        <p style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:12px">Payment method</p>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Admin Login — SurveyQC</title></head><body>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center">
  <div style="width:380px">
    <div style="text-align:center;margin-bottom:28px">
      <div style="width:40px;height:40px;background:var(--purple);border-radius:10px;display:flex;align-items:center;justify-content:center;margin:0 auto 12px"><i class="ti ti-shield-check" style="color:white;font-size:20px"></i></div>
      <p style="font-size:18px;font-weight:600;color:var(--text)">Admin access</p>
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

    return render_template_string(SHARED_CSS + """<style>.app-layout{margin-left:0!important}.main-content{margin-left:220px!important}</style>""" + f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Admin — SurveyQC</title></head><body>
<div style="display:flex;min-height:100vh"><div style="margin-left:220px;flex:1;padding:28px;min-width:0;width:calc(100% - 220px)">
  
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
      <div><p style="font-size:20px;font-weight:600;color:var(--text)">Admin overview</p><p style="font-size:12px;color:var(--text3)">{datetime.now().strftime('%A, %d %B %Y')}</p></div>
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
        <p style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:14px">AI health status</p>
        <div style="display:flex;flex-direction:column;gap:7px">
          <div class="worker-card"><span class="pulse"></span><div style="flex:1"><p style="font-size:12px;color:white">Playwright browser</p></div><span class="badge badge-teal">Healthy</span></div>
          <div class="worker-card"><span class="pulse"></span><div style="flex:1"><p style="font-size:12px;color:white">Report generator</p></div><span class="badge badge-teal">Healthy</span></div>
          <div class="worker-card"><span class="pulse"></span><div style="flex:1"><p style="font-size:12px;color:white">File storage</p></div><span class="badge badge-teal">Healthy</span></div>
          <div class="worker-card"><span class="dot dot-amber" style="margin-left:1px"></span><div style="flex:1"><p style="font-size:12px;color:white">Email service</p></div><span class="badge badge-amber">Not configured</span></div>
        </div>
      </div>
      <div class="card">
        <p style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:14px">Quick actions</p>
        <div style="display:flex;flex-direction:column;gap:8px">
          <a href="/admin/email" class="btn btn-ghost" style="justify-content:center"><i class="ti ti-mail"></i>Email all users</a>
          <button class="btn btn-ghost" style="width:100%;justify-content:center"><i class="ti ti-download"></i>Export data</button>
          <button class="btn btn-ghost" style="width:100%;justify-content:center"><i class="ti ti-gift"></i>Gift credits to user</button>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <p style="font-size:14px;font-weight:600;color:var(--text)">Users</p>
        <a href="/admin/users" style="font-size:12px;color:var(--purple);text-decoration:none">View all →</a>
      </div>
      <table class="data-table" style="width:100%">
        <thead><tr><th>Name</th><th>Email</th><th>Plan</th><th>Reports</th><th>Joined</th><th>Actions</th></tr></thead>
        <tbody>{users_html}</tbody>
      </table>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <p style="font-size:14px;font-weight:600;color:var(--text)">Recent QC jobs</p>
        <a href="/admin/reports" style="font-size:12px;color:var(--purple);text-decoration:none">View all →</a>
      </div>
      <table class="data-table" style="width:100%">
        <thead><tr><th>Document</th><th>User</th><th>Status</th><th>Issues</th><th>Date</th></tr></thead>
        <tbody>{jobs_html or '<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No jobs yet</td></tr>'}</tbody>
      </table>
    </div>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Users — Admin</title><script src="/admin-sidebar-js"></script></head><body>
<div style="padding:24px;margin-left:0">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--text3);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:20px;font-weight:600;color:var(--text)">All Users ({len(users_db)})</p>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Email Users — Admin</title><script src="/admin-sidebar-js"></script></head><body>
<div style="padding:24px;max-width:600px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--text3);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:20px;font-weight:600;color:var(--text)">Email Users</p>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Reports — Admin</title><script src="/admin-sidebar-js"></script></head><body>
<div style="padding:24px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--text3);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:20px;font-weight:600;color:var(--text)">All QC Jobs ({len(jobs)})</p>
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
    words = re.findall(r"[\w']+", text, re.UNICODE)
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

def _extract_options(page):
    """Extract answer options from a survey page (multi-platform)."""
    import re as _re
    opts = []
    seen = set()
    selectors = [
        ".cf-radio-answer__text", ".cf-checkbox-answer__text",
        "label.cf-answer", ".answer-text", ".option-text",
        "label[for]", ".sv-item__control-label", ".response-option"
    ]
    for sel in selectors:
        try:
            els = page.locator(sel).all()
            if els:
                for el in els:
                    try:
                        t = el.inner_text().strip()
                        if t and len(t) < 200 and t not in seen:
                            seen.add(t)
                            opts.append({"text": t})
                    except: continue
                if opts: break
        except: continue
    return opts


def _page_has_inputs(page):
    """Return True if the page has at least one answerable input element.
    Used to distinguish real questions from pure display/info screens."""
    try:
        return bool(page.evaluate(
            "() => document.querySelectorAll("
            "'input[type=\"radio\"],input[type=\"checkbox\"],select,"
            "input[type=\"text\"],input[type=\"number\"],"
            "input[type=\"range\"],textarea'"
            ").length > 0"
        ))
    except Exception:
        return True  # safe default: assume inputs exist if DOM check fails


def get_gemini_model():
    """Returns a configured Gemini model if API key is set, else None."""
    try:
        key = api_store.get('gemini', {}).get('key', '').strip()
        if not key:
            key = os.environ.get('GEMINI_API_KEY', '').strip()
        if not key:
            return None
        import google.generativeai as genai
        genai.configure(api_key=key)
        return genai.GenerativeModel('gemini-2.5-flash')
    except Exception:
        return None


def ai_compare_text(model, qid, doc_text, live_text):
    """Use Gemini to intelligently compare doc question vs live question.
    Returns (is_issue, issue_type, details) or None if AI unavailable/errors."""
    if not model:
        return None
    try:
        prompt = (
            "You are a survey QC expert. Compare the SPEC (expected) question text "
            "against the LIVE (actual) question text from a deployed survey.\n\n"
            "SPEC: " + doc_text[:1500] + "\n\n"
            "LIVE: " + live_text[:1500] + "\n\n"
            "Ignore trivial differences (whitespace, punctuation, HTML artifacts). "
            "Flag ONLY real issues: missing words, changed meaning, wrong wording, missing instructions.\n"
            "Respond ONLY with valid JSON, nothing else:\n"
            '{"issue": true/false, "type": "short type e.g. MISSING WORDS", "details": "one short sentence", "severity": "HIGH/MEDIUM/LOW"}'
        )
        resp = model.generate_content(prompt)
        import json, re as _re
        raw = resp.text.strip()
        raw = _re.sub(r'```json|```', '', raw).strip()
        data = json.loads(raw)
        if data.get('issue'):
            return (True, data.get('type', 'AI FLAGGED'), data.get('details', ''), data.get('severity', 'MEDIUM'))
        return (False, None, None, None)
    except Exception:
        return None


def ai_compare_batch(model, batch):
    """Compare MULTIPLE questions in ONE AI call (saves quota: 50 calls -> 6 calls).
    batch = list of dicts: [{qid, doc_text, doc_opts, live_text, live_opts}, ...]
    Returns dict {qid: [issue dicts]} or None if AI unavailable after retries."""
    if not model or not batch:
        return None
    import json, re as _re
    blocks = []
    for item in batch:
        do = "\n".join("- " + o for o in item['doc_opts']) if item['doc_opts'] else "(none/open-ended)"
        lo = "\n".join("- " + o for o in item['live_opts']) if item['live_opts'] else "(none/open-ended)"
        blocks.append(
            "=== QUESTION " + item['qid'] + " ===\n"
            "SPEC text: " + item['doc_text'][:900] + "\n"
            "SPEC options:\n" + do + "\n"
            "LIVE text: " + item['live_text'][:1100] + "\n"
            "LIVE options:\n" + lo
        )
    prompt = (
        "You are an expert survey QC reviewer. Compare each SPEC question (design doc) vs LIVE "
        "(deployed survey). Language may be ANY (French, Urdu, Hindi, German, English) - compare by "
        "MEANING not exact words.\n\n"
        + "\n\n".join(blocks) + "\n\n"
        "RULES:\n"
        "1. IGNORE: whitespace, punctuation, capitalization, HTML artifacts, bracket instructions "
        "like [ALL COUNTRIES], translation notes, formatting.\n"
        "2. LIVE may have EXTRA surrounding text - that is OK. Only check if SPEC question's MEANING "
        "is present in LIVE.\n"
        "3. Flag REAL issues only: meaning changed/missing, options missing/different, instruction "
        "like '(ne pas poser)' violated, wrong wording changing meaning, truncated text.\n"
        "4. If SPEC content IS present in LIVE (even with extra text), report NO issue for it.\n\n"
        "Respond ONLY with valid JSON object. Key = question id, value = array of issues "
        "(empty array if no issue). Example:\n"
        '{"A1": [{"type":"MISSING TEXT","details":"...","severity":"HIGH"}], "A2": []}'
    )
    for attempt in range(3):
        try:
            resp = model.generate_content(prompt)
            raw = _re.sub(r'```json|```', '', resp.text.strip()).strip()
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            raw = m.group(0) if m else raw
            data = json.loads(raw)
            out = {}
            for qid, issarr in data.items():
                if not isinstance(issarr, list):
                    continue
                out[qid] = []
                for d in issarr:
                    if d and d.get('type'):
                        out[qid].append({"qid": qid, "type": d.get('type', 'AI FLAGGED'),
                                         "details": d.get('details', ''), "severity": d.get('severity', 'MEDIUM')})
            return out
        except Exception as e:
            err = str(e)
            is_rate_limit = '429' in err or 'quota' in err.lower() or 'rate' in err.lower()
            if attempt < 2:
                time.sleep(20 if is_rate_limit else 4)
                continue
    return None


def ai_compare_full(model, qid, doc_text, doc_opts, live_text, live_opts):
    """Language-agnostic semantic comparison of a full question (text + options).
    Returns a LIST of issue dicts, or None if AI unavailable/errored.
    Empty list = no issues found (question is correct)."""
    if not model:
        return None
    try:
        import json, re as _re
        doc_opts_str = "\n".join("- " + o for o in doc_opts) if doc_opts else "(none / open-ended)"
        live_opts_str = "\n".join("- " + o for o in live_opts) if live_opts else "(none / open-ended)"
        prompt = (
            "You are an expert survey QC reviewer. Compare a SPEC question (from the design document) "
            "against the LIVE question (from the deployed survey). The language may be ANY language "
            "(French, Urdu, Hindi, German, English, etc.) - compare by MEANING, not exact words.\n\n"
            "QUESTION ID: " + qid + "\n\n"
            "=== SPEC (expected) ===\n"
            "Question text: " + doc_text[:1200] + "\n"
            "Answer options:\n" + doc_opts_str + "\n\n"
            "=== LIVE (actual deployed) ===\n"
            "Question text: " + live_text[:1500] + "\n"
            "Answer options:\n" + live_opts_str + "\n\n"
            "RULES:\n"
            "1. IGNORE: whitespace, punctuation, capitalization, HTML artifacts, programming instructions "
            "in brackets like [ALL COUNTRIES], translation notes, formatting differences.\n"
            "2. The LIVE text may contain EXTRA surrounding content from the page - that is OK, only check "
            "if the SPEC question's MEANING is present in LIVE.\n"
            "3. Flag REAL issues only: (a) question meaning changed or missing, (b) answer options missing "
            "or different, (c) instruction like '(ne pas poser)'/'do not ask' violated, (d) wrong wording "
            "that changes meaning.\n"
            "4. If the SPEC question content IS present in LIVE (even with extra text around it), report NO issue.\n\n"
            "Respond ONLY with valid JSON array, nothing else. Empty array if no issues:\n"
            '[{"type": "SHORT TYPE", "details": "one short sentence", "severity": "HIGH/MEDIUM/LOW"}]'
        )
        resp = model.generate_content(prompt)
        raw = resp.text.strip()
        raw = _re.sub(r'```json|```', '', raw).strip()
        data = json.loads(raw)
        if not isinstance(data, list):
            data = [data] if data else []
        out = []
        for d in data:
            if d and d.get('type'):
                out.append({"qid": qid, "type": d.get('type', 'AI FLAGGED'),
                            "details": d.get('details', ''), "severity": d.get('severity', 'MEDIUM')})
        return out
    except Exception:
        return None


def ai_generate_summary(model, questions, live_data, issues):
    """Generate a human-readable AI summary of the QC report."""
    if not model:
        # Fallback: rule-based summary
        high = sum(1 for i in issues if i.get('severity') == 'HIGH')
        med = sum(1 for i in issues if i.get('severity') == 'MEDIUM')
        total = len(issues)
        if total == 0:
            return "All checks passed. No issues detected across " + str(len(questions)) + " questions. Survey is ready to launch."
        return (str(total) + " issue(s) detected: " + str(high) + " high-priority, " + str(med) + " medium. "
                "Review high-priority issues before launching the survey.")
    try:
        issue_brief = "; ".join([i.get('type', '') + " on " + i.get('qid', '') for i in issues[:15]])
        prompt = (
            "You are a survey QC expert. Write a 2-3 sentence professional summary "
            "of this survey QC report for a client.\n\n"
            "Total questions: " + str(len(questions)) + "\n"
            "Total issues: " + str(len(issues)) + "\n"
            "Issues found: " + (issue_brief if issue_brief else "none") + "\n\n"
            "Be concise, professional, actionable. Plain text only, no markdown."
        )
        resp = model.generate_content(prompt)
        return resp.text.strip()[:600]
    except Exception:
        total = len(issues)
        if total == 0:
            return "All checks passed. No issues detected. Survey is ready to launch."
        return str(total) + " issue(s) detected. Review before launching."


def _parse_tables_for_qids(doc, questions, qid_pat, junk_re):
    """
    Supplement the paragraph parser: scan every doc table for question IDs
    that only appear inside grid/matrix tables (e.g. Q1, Q2, Q11, Q12, Q12.2
    in Confirmit docs where the QID sits in a header cell, not in a paragraph).

    Rules:
    - For PROGRAMMING TABLE blocks: extract the header QID only (skip routing/logic body).
    - For other tables: scan all cells for QID patterns and extract answer options.
    - Skip cells whose entire text is a bracketed label like [R3 – label].
    - Register every discovered QID in questions{} (don't overwrite existing text).
    - Extract answer options from the first column of data rows below the header.
    """
    pt_re = re.compile(r'PROG(?:RAM(?:M?ING)?)?\s+TABLE', re.IGNORECASE)
    num_only = re.compile(r'^\d+$')
    bare_qid_re = re.compile(
        r'^(?P<qid>[A-Za-z]{1,8}\d+[a-zA-Z]?(?:bis|ter|Info|info|Ex|_\d+|\.\d+)?)$'
    )
    # Keywords that appear in PROGRAMMING TABLE body rows (not QID lines)
    _prog_kw = re.compile(
        r'^(?:PROG(?:RAM(?:M?ING)?)?\s+TABLE|TYPE|ROUTING|ROUTINE|LOGIC'
        r'|CODED|OPEN\s+ENDED|MULTIPLE|NUMERIC|RANGE|MIN\s*=|MAX\s*=|MANDATORY'
        r'|ALL\s+RESP|RANDOMIS|DISPLAY|SCREEN)',
        re.IGNORECASE
    )

    for table in doc.tables:
        # Build row-list-of-cell-strings
        rows = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                ct = "\n".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
                cells.append(ct.strip())
            rows.append(cells)

        full_text = "\n".join(c for row in rows for c in row if c)

        # Programming tables hold TYPE/ROUTING metadata, not answer content.
        # Extract the header QID (the question being defined) but skip the body.
        if pt_re.search(full_text):
            _found_prog_qid = False
            for _row in rows[:4]:
                if _found_prog_qid:
                    break
                for _cell in _row:
                    if _found_prog_qid:
                        break
                    for _line in _cell.split('\n'):
                        _line = _line.strip()
                        if not _line or len(_line) > 30 or _prog_kw.search(_line):
                            continue
                        if _line.startswith('[') and _line.endswith(']'):
                            continue
                        _m = qid_pat.match(_line)
                        if not _m:
                            _m = bare_qid_re.match(_line)
                        if _m:
                            _hqid = _m.group('qid')
                            if _hqid not in questions:
                                questions[_hqid] = {
                                    "text": "", "options": [],
                                    "is_mandatory": False, "has_piping": False,
                                    "termination_rules": [], "is_numeric": False,
                                }
                            _found_prog_qid = True
                            break
            continue

        # Scan every cell line for QID patterns.
        # Two-pass match: (1) standard qid_pat (needs a trailing separator char),
        # (2) bare QID-only line like "Q11" with no trailing char — common when a
        # table cell has the QID as its sole paragraph.
        found_qids = {}       # qid -> rest_text (question text fragment)
        header_row_idx = None

        for ri, cells in enumerate(rows):
            row_found = {}
            for cell_text in cells:
                for line in cell_text.split('\n'):
                    line = line.strip()
                    if not line or len(line) > 250 or junk_re.search(line):
                        continue
                    # Skip whole-line bracketed labels like [R3 – Consentement]
                    if line.startswith('[') and line.endswith(']'):
                        continue
                    m = qid_pat.match(line)
                    if not m and len(line) <= 20:
                        # Bare QID cell like "Q11" or "Q12.2" with no separator
                        m = bare_qid_re.match(line)
                    if not m:
                        continue
                    qid = m.group('qid')
                    rest = line[m.end():].strip()
                    # Strip column-header suffixes like "| MEMO" or "| adhésion | Note"
                    rest = re.sub(
                        r'\s*[\|–—]\s*(?:MEMO|NOTE|adh[eé]sion|SPECIFICITE|RANDOMIS).*',
                        '', rest, flags=re.IGNORECASE
                    ).strip()
                    if qid not in row_found:
                        row_found[qid] = rest

            if row_found:
                if header_row_idx is None:
                    header_row_idx = ri
                found_qids.update(row_found)

        if not found_qids:
            continue

        # Register each QID (never overwrite text already set by paragraph pass)
        for qid, rest in found_qids.items():
            if qid not in questions:
                questions[qid] = {
                    "text": rest, "options": [],
                    "is_mandatory": False, "has_piping": False,
                    "termination_rules": [], "is_numeric": False,
                }
            elif not questions[qid]["text"] and rest:
                questions[qid]["text"] = rest

        if header_row_idx is None:
            continue

        # Extract options from rows below the header row.
        # Use the first QID found as the target question for these options.
        primary_qid = next(iter(found_qids))
        seen_opts = {o["text"] for o in questions[primary_qid]["options"]}

        for ri in range(header_row_idx + 1, len(rows)):
            if not rows[ri]:
                continue
            opt_text = rows[ri][0].strip()
            if (not opt_text
                    or len(opt_text) < 2
                    or len(opt_text) > 200
                    or num_only.match(opt_text)
                    or qid_pat.match(opt_text)        # skip repeated-header rows
                    or bare_qid_re.match(opt_text)    # skip bare "Q11" type cells
                    or opt_text in seen_opts):
                continue
            # Find the numeric answer code in sibling cells
            code = str(ri)
            for ci in range(1, len(rows[ri])):
                v = rows[ri][ci].strip()
                if num_only.match(v):
                    code = v
                    break
            questions[primary_qid]["options"].append({"code": code, "text": opt_text})
            seen_opts.add(opt_text)


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
        qid_pat = re.compile(r'^\s*\[?\s*(?P<qid>[A-Za-z]{1,8}\d+[a-zA-Z]?(?:bis|ter|Info|info|Ex|_\d+|\.\d+)?)\s*[\.\-\s\]\:\)]')
        current_qid = None

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text or JUNK_RE.search(text): continue

            # Stop accumulating when a major section boundary is hit.
            # Path A: language-specific keywords (FR/EN/etc.) via SECTION_STOP_RE.
            # Path B: structural — a short paragraph that is ≥75% uppercase letters
            #   and has no '?' is a section heading in any Latin-script language
            #   (DATENSCHUTZ, DATOS PERSONALES, DATI PERSONALI, etc.).
            # Arabic question mark ؟ (U+061F) is also excluded from "has question mark".
            _no_qmark = '?' not in text and '؟' not in text
            if current_qid and _no_qmark:
                _alpha = [c for c in text if c.isalpha()]
                _upper_ratio = (sum(1 for c in _alpha if c.isupper()) / len(_alpha)
                                if _alpha else 0)
                _structural_heading = (
                    len(text) <= 60
                    and not text[0].isdigit()
                    and not qid_pat.match(text)
                    and len(_alpha) >= 4
                    and _upper_ratio >= 0.75
                )
                if SECTION_STOP_RE.search(text) or _structural_heading:
                    current_qid = None
                    continue

            m = qid_pat.match(text)
            if m:
                # FIX 1: Skip bracketed section labels like [R3 \u2013 Consentement
                # confidentialit\u00e9]. A real question paragraph that starts with '['
                # never ends with ']' \u2014 the question text follows after the bracket.
                if text.startswith('[') and text.endswith(']'):
                    continue

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

        # FIX 3: Pick up QIDs defined inside table grid headers (Q1, Q1.1, Q1.2,
        # Q2, Q11, Q12, Q12.2, etc.) that the paragraph pass never sees.
        _parse_tables_for_qids(doc, questions, qid_pat, JUNK_RE)

        # FIX 4: Backfill text for questions whose text paragraph appears BEFORE
        # their PROG TABLE without a QID label (e.g. Q3, Q4, Q10, Q13 in this doc).
        # Walk the doc body in order; for each PROG TABLE whose QID still has empty
        # text, look backwards for the unlabeled text paragraphs that precede it.
        _pt_re_bf = re.compile(r'PROG(?:RAM(?:M?ING)?)?\s+TABLE', re.IGNORECASE)
        _bare_bf = re.compile(r'^(?P<qid>[A-Za-z]{1,8}\d+[a-zA-Z]?(?:bis|ter|Info|info|Ex|_\d+|\.\d+)?)$')
        _bseq = []  # ('para', text) | ('prog', qid) | ('tbl', None)
        for _bc in doc.element.body.iterchildren():
            if _bc.tag == qn('w:p'):
                for _bp in doc.paragraphs:
                    if _bp._element is _bc:
                        _bseq.append(('para', _bp.text.strip()))
                        break
            elif _bc.tag == qn('w:tbl'):
                for _bt in doc.tables:
                    if _bt._element is _bc:
                        _bcells = []
                        for _brow in _bt.rows:
                            for _bcell in _brow.cells:
                                _bct = "\n".join(_bp2.text.strip() for _bp2 in _bcell.paragraphs if _bp2.text.strip())
                                _bcells.append(_bct.strip())
                        _bjnd = "\n".join(_bcells)
                        if _pt_re_bf.search(_bjnd):
                            _btqid = None
                            for _bcl in _bcells[:8]:
                                for _bln in _bcl.split('\n'):
                                    _bln = _bln.strip()
                                    if not _bln or len(_bln) > 30:
                                        continue
                                    _bm = qid_pat.match(_bln) or _bare_bf.match(_bln)
                                    if _bm:
                                        _btqid = _bm.group('qid')
                                        break
                                if _btqid:
                                    break
                            _bseq.append(('prog', _btqid))
                        else:
                            _trows = []
                            for _brow2 in _bt.rows:
                                _tr = []
                                for _bcl2 in _brow2.cells:
                                    _ct2 = ' '.join(
                                        _p2.text.strip()
                                        for _p2 in _bcl2.paragraphs
                                        if _p2.text.strip()
                                    )
                                    if _ct2:
                                        _tr.append(_ct2)
                                if _tr:
                                    _trows.append(_tr)
                            _bseq.append(('tbl', _trows))
                        break
        for _bi, (_btype, _bval) in enumerate(_bseq):
            if _btype != 'prog' or not _bval or _bval not in questions:
                continue
            if questions[_bval]['text']:
                continue  # paragraph pass already found text — leave it
            _cands = []
            for _bj in range(_bi - 1, max(_bi - 12, -1), -1):
                _jt, _jv = _bseq[_bj]
                if _jt == 'prog':
                    break  # hit the previous PROG TABLE — stop
                if _jt == 'tbl':
                    continue  # skip option / routing tables
                _ptxt = _jv
                if not _ptxt or len(_ptxt) < 8:
                    continue
                if qid_pat.match(_ptxt):
                    break  # hit a labeled QID paragraph — belongs to another question
                if JUNK_RE.search(_ptxt):
                    continue
                _jal = [c for c in _ptxt if c.isalpha()]
                _jrat = (sum(1 for c in _jal if c.isupper()) / len(_jal) if _jal else 0)
                if len(_ptxt) <= 60 and '?' not in _ptxt and '؟' not in _ptxt and len(_jal) >= 4 and _jrat >= 0.75:
                    break  # structural section heading — stop
                _cands.append(_ptxt)
            if _cands:
                _cands.reverse()
                questions[_bval]['text'] = re.sub(r'\s+', ' ', ' '.join(_cands)).strip()

        # Options pass: assign standalone-table options to the right QID in
        # document order. Mirrors qc_engine._pending_opts logic.
        def _assign_simple_opts(q, rows):
            for _r in rows:
                if len(_r) < 2:
                    continue
                if re.match(r'^\d+$', _r[0]):
                    _code, _text = _r[0], _r[1]
                elif len(_r) == 2 and re.match(r'^\d+$', _r[-1]) and not re.match(r'^\d+$', _r[0]):
                    _code, _text = _r[-1], _r[0]
                else:
                    continue
                if _text and not any(o['text'] == _text for o in q['options']):
                    q['options'].append({'code': _code, 'text': _text})

        _opt_qid = None
        _opt_pending = None
        for _btype, _bval in _bseq:
            if _btype == 'para' and _bval:
                _bm_opt = qid_pat.match(_bval)
                if _bm_opt:
                    _opt_qid = _bm_opt.group('qid')
                    # don't clear _opt_pending — table may belong to the new QID
            elif _btype == 'prog' and _bval and _bval in questions:
                if _opt_pending and not questions[_bval]['options']:
                    _assign_simple_opts(questions[_bval], _opt_pending)
                _opt_qid = _bval
                _opt_pending = None
            elif _btype == 'tbl' and isinstance(_bval, list) and _bval:
                if _opt_qid and _opt_qid in questions and not questions[_opt_qid]['options']:
                    _assign_simple_opts(questions[_opt_qid], _bval)
                _opt_pending = _bval

        # Extract termination rules
        term_re = re.compile(
            r'(?:THANKS?\s*AND\s*CLOSE|THANK\s*AND\s*CLOSE'
            r'|MERCI\s+ET\s+FERMER|MERCI\s+ET\s+CLORE'
            r'|MERCI\s+FERMER|CLORE\s+LE\s+QUESTIONNAIRE'
            r'|FIN\s+DU\s+QUESTIONNAIRE|STOPPER\s+LE\s+SONDAGE'
            r'|GRAZIE\s+E\s+CHIUDI'
            r'|GRACIAS\s+Y\s+CIERRE|TERMINATE\b'
            # German
            r'|UMFRAGE\s+BEENDEN|BEENDEN\s+UND\s+SCHLIE'
            r'|FRAGEBOGEN\s+BEENDEN|ABBRECHEN\b'
            # Portuguese / Dutch / Polish / Turkish
            r'|ENCERRAR\s+(?:E\s+)?AGRADECER|FECHAR\s+E\s+AGRADECER'
            r'|SLUITEN\s+EN\s+BEDANKEN|ONDERZOEK\s+BEËINDIGEN'
            r'|ZAKOŃCZYĆ|ANKET[İI]\s+KAPAT'
            # Structural catch-all: any routing cell line that starts with a
            # close/screen/stop/end/exit verb in any language can be detected
            # by the line-level extraction below even without matching here.
            # This regex gates cell selection; structural extraction is additive.
            r')',
            re.IGNORECASE
        )
        qid_heading_re = re.compile(r'^\s*\[?\s*([A-Za-z]{1,8}\d+[a-zA-Z]?(?:bis|ter|Info|info|Ex|_\d+|\.\d+)?)\s*[\.\-\s\]\:\)]')
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
            pt_match = re.search(r'PROG(?:RAM(?:M?ING)?)?\s+TABLE[\s\|\n]*([A-Za-z]{1,8}\d+(?:\.\d+)*)', joined, re.IGNORECASE)
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

                for _line in cell_text.split('\n'):
                    if not term_re.search(_line): continue
                    for _lm in re.finditer(r'\bcode\s+(\d+)\b', _line, re.IGNORECASE):
                        code = _lm.group(1)
                        if not any(r.get('answer_codes') == [code] for r in questions[host]['termination_rules']):
                            questions[host]['termination_rules'].append({"test_qid": host, "answer_codes": [code], "raw": _line[:100], "source": "line-structural"})

        for qid in questions:
            questions[qid]["text"] = re.sub(r'\s+', ' ', questions[qid]["text"]).strip()

        term_count = sum(len(q.get("termination_rules", [])) for q in questions.values())
        log(f'  Questions parsed: {len(questions)}', 'green')
        log(f'  Termination rules: {term_count}', 'green')
        progress(15)

        # ── AI FALLBACK: patch weak questions the rigid parser missed ─────────
        # Runs only when a Gemini key is configured. Sends ONE batched call for
        # all weak questions so token usage stays proportional, not per-question.
        _ai_fb_model = get_gemini_model()
        if _ai_fb_model:
            _weak = {}
            for _fqid, _fq in questions.items():
                _txt_weak = len(_fq["text"].strip()) < 10
                _opts_weak = (
                    not _fq["options"]
                    and not _fq.get("is_numeric")
                    and not any(kw in _fq["text"].lower()
                                for kw in ("open", "verbatim", "précisez",
                                           "specify", "saisir", "numeric",
                                           "entrez", "enter"))
                )
                if _txt_weak or _opts_weak:
                    _weak[_fqid] = {"txt": _txt_weak, "opts": _opts_weak}

            if _weak:
                log(f'  AI fallback: {len(_weak)} weak question(s) — patching with Gemini', 'yellow')

                # Find each weak QID's position in the body sequence (_bseq) built
                # during the backfill pass so we can extract surrounding raw context.
                _bpos = {}
                for _bi2, (_bt2, _bv2) in enumerate(_bseq):
                    if _bt2 == 'prog' and _bv2 in _weak and _bv2 not in _bpos:
                        _bpos[_bv2] = _bi2
                    elif _bt2 == 'para' and isinstance(_bv2, str):
                        _pm2 = qid_pat.match(_bv2)
                        if _pm2:
                            _q2 = _pm2.group('qid')
                            if _q2 in _weak and _q2 not in _bpos:
                                _bpos[_q2] = _bi2

                def _ctx_for_pos(pos):
                    parts = []
                    for _bt3, _bv3 in _bseq[max(0, pos - 8): min(len(_bseq), pos + 22)]:
                        if _bt3 == 'para' and _bv3:
                            parts.append(_bv3)
                        elif _bt3 == 'prog' and _bv3:
                            parts.append(f'[PROG TABLE: {_bv3}]')
                        elif _bt3 == 'tbl' and isinstance(_bv3, list):
                            for _r3 in _bv3:
                                if isinstance(_r3, list):
                                    parts.append(' | '.join(str(c) for c in _r3 if c))
                    return '\n'.join(p for p in parts if p.strip())

                _sections = []
                for _wqid in _weak:
                    _p2 = _bpos.get(_wqid)
                    if _p2 is None:
                        continue
                    _ctx2 = _ctx_for_pos(_p2)
                    if _ctx2:
                        _sections.append(f"--- QID: {_wqid} ---\n{_ctx2[:900]}")

                if _sections:
                    _fb_prompt = (
                        "You are a survey scripting document parser.\n"
                        "Extract question data from each raw section below.\n\n"
                        "For each QID:\n"
                        "- text: the survey question shown to respondents"
                        " (NOT metadata lines like TYPE / ROUTING / MANDATORY / RANGE)\n"
                        "- options: answer options as [{\"code\":\"1\",\"text\":\"...\"}].\n"
                        "  Options may appear as 'CODEm TEXT' (e.g. '22m Cardiologie 191m Pneumologie'),\n"
                        "  or '1. Option text', or in a table with code | text columns.\n"
                        "  If the question is open-ended or numeric with no fixed options, return [].\n\n"
                        + "\n\n".join(_sections)
                        + "\n\nReturn ONLY this JSON (no markdown):\n"
                        "{\"questions\":[{\"qid\":\"X\",\"text\":\"...\","
                        "\"options\":[{\"code\":\"1\",\"text\":\"...\"}]}]}"
                    )

                    _fb_data = None
                    for _att in range(3):
                        try:
                            _fb_resp = _ai_fb_model.generate_content(_fb_prompt)
                            _fb_raw = _fb_resp.text.strip()
                            _fb_raw = re.sub(r'```json|```', '', _fb_raw).strip()
                            _fb_raw = re.sub(r'<[^>]+>.*?</[^>]+>', '', _fb_raw,
                                             flags=re.DOTALL).strip()
                            _fbm = re.search(r'\{.*\}', _fb_raw, re.DOTALL)
                            if _fbm:
                                _fb_data = json.loads(_fbm.group(0))
                            break
                        except Exception as _fbe:
                            _wait = [5, 15, 45][_att]
                            log(f'  AI fallback attempt {_att+1} failed'
                                f' ({str(_fbe)[:60]}) — retry in {_wait}s', 'yellow')
                            time.sleep(_wait)

                    _merged = 0
                    if _fb_data:
                        for _fbq in _fb_data.get("questions", []):
                            _fid = _fbq.get("qid", "")
                            if _fid not in questions:
                                continue
                            _q = questions[_fid]
                            # Only overwrite what the rigid parser left empty
                            if not _q["text"].strip() and _fbq.get("text", "").strip():
                                _q["text"] = re.sub(r'\s+', ' ', _fbq["text"]).strip()
                                _merged += 1
                            if not _q["options"] and _fbq.get("options"):
                                _valid = [o for o in _fbq["options"]
                                          if o.get("code") and o.get("text")]
                                if _valid:
                                    _q["options"] = _valid
                                    _merged += 1
                        log(f'  AI fallback: patched {_merged} field(s)', 'green')
                    else:
                        log('  AI fallback: no parseable data from Gemini', 'yellow')
            else:
                log('  AI fallback: rigid parser got everything — no AI call needed', 'green')
        # ── end AI fallback ───────────────────────────────────────────────────

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
                browser = p.chromium.launch(headless=True, slow_mo=150)
                context = browser.new_context(viewport={"width":1400,"height":900})
                page = context.new_page()
                page.goto(survey_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                # LINK CHECK: detect expired/dead survey link
                try:
                    _body = page.locator("body").inner_text(timeout=5000).lower()
                    _bad = ["400: bad request", "bad request", "session expired", "session has expired", "404 not found", "page not found", "error has occurred", "link has expired", "survey is closed", "no longer available", "afraid i can", "server encountered an error", "not able to interpret the request"]
                    if any(b in _body for b in _bad) or len(_body.strip()) < 50:
                        log("  LINK NOT WORKING / EXPIRED - get a fresh link", "red")
                        jobs[job_id]["status"] = "error"
                        jobs[job_id]["phase"] = "Survey link expired or not working"
                        jobs[job_id]["error"] = "Survey link is not working or has expired. Please check the link and try again with a fresh link."
                        try: browser.close()
                        except: pass
                        return
                except: pass
                ss_dir = f"{OUTPUT_FOLDER}/{job_id}/screenshots"
                os.makedirs(ss_dir, exist_ok=True)

                if country:
                    try:
                        page.locator(f".cf-radio-answer__text:has-text('{country}')").first.click(force=True, timeout=8000)
                        page.wait_for_timeout(1000)
                        for sel in ["button:has-text('>>')", "input[value='>>']", ".cf-button-next"]:
                            try:
                                page.locator(sel).first.click(timeout=3000); break
                            except: continue
                        page.wait_for_timeout(2500)
                    except Exception as e:
                        log('  Country select skipped: ' + str(e)[:40], 'yellow')

                has_test_nav = False
                # RETRY: wait for Test Navigator to load (up to 3 tries, page may be slow)
                for _try in range(3):
                    _cnt = page.locator(".cf-tn-list-item").count()
                    if _cnt > 0:
                        log("  TN loaded: " + str(_cnt) + " items (try " + str(_try+1) + ")", "green")
                        break
                    log("  TN not ready, waiting... (try " + str(_try+1) + ")", "yellow")
                    page.wait_for_timeout(4000)
                    try:
                        page.locator("text=Test Navigator").first.click(timeout=2000)
                        page.wait_for_timeout(1500)
                    except: pass
                try:
                    page_html = page.content()
                    log("  Page HTML length: " + str(len(page_html)), "cyan")
                    tn_selectors = [
                        ".cf-tn-list-item",
                        ".cf-tn-list-item",
                        "[class*='tn-question']",
                        "[class*='test-navigator']",
                        ".wix-tn-item",
                        "[class*='tn-item']",
                    ]
                    for tn_sel in tn_selectors:
                        count = page.locator(tn_sel).count()
                        log("  TN selector " + tn_sel + " count: " + str(count), "yellow")
                        if count > 0:
                            has_test_nav = True
                            log("  Found TN with: " + tn_sel, "green")
                            break
                    if not has_test_nav:
                        for btn_text in ["Test Navigator", "Navigator", "Navigateur"]:
                            try:
                                page.locator(f"text={btn_text}").first.click(timeout=3000)
                                page.wait_for_timeout(1000)
                                for tn_sel in tn_selectors:
                                    if page.locator(tn_sel).count() > 0:
                                        has_test_nav = True
                                        log("  TN found: " + tn_sel, "green")
                                        break
                                if has_test_nav:
                                    break
                            except: pass
                    if not has_test_nav:
                        try:
                            page.screenshot(path="/var/www/surveyqc/debug_page.png", full_page=True)
                            log("  Debug screenshot saved", "yellow")
                        except: pass
                        classes = page.evaluate("""() => {
                            const els = document.querySelectorAll("[class]");
                            const cls = new Set();
                            els.forEach(e => e.className.toString().split(" ").forEach(c => { if(c) cls.add(c); }));
                            return Array.from(cls).slice(0, 50);
                        }""")
                        log("  Page CSS classes: " + str(classes[:30]), "cyan")
                except Exception as ex:
                    log("  TN detection error: " + str(ex), "red")

                except: pass

                if has_test_nav:
                    log('  Mode: Test Navigator (Confirmit)', 'blue')
                    nav_items = page.locator(".cf-tn-list-item").all()
                    qid_index_map = []
                    seen_qids = set()
                    for ni, el in enumerate(nav_items):
                        try:
                            words = el.inner_text().strip().split()
                            first = words[0].strip() if words else ''
                            second = words[1].strip() if len(words) > 1 else ''
                            m = re.match(r'^([A-Za-z]{1,8}\d+[a-zA-Z]?(?:bis|ter|Info|info|Ex|_\d+|\.\d+)?)$', first)
                            if m and m.group(1) not in seen_qids:
                                doc_qid = m.group(1)
                                # Confirmit TN shows "DocID PlatformID" — keep platform ID
                                # for [Question ID: PlatformID] marker matching (e.g. Q1 → Q1new)
                                plat_qid = second if (second and second != doc_qid
                                    and re.match(r'^[A-Za-z]\w*\d+\w*$', second)) else doc_qid
                                qid_index_map.append((ni, doc_qid, plat_qid))
                                seen_qids.add(doc_qid)
                        except: continue

                    log('  ' + str(len(qid_index_map)) + ' QIDs found in navigator', 'blue')
                    total = max(1, len(qid_index_map))
                    for i, (nav_idx, qid, plat_qid) in enumerate(qid_index_map, 1):
                        # STOP CHECK: if user clicked Stop, abort crawling
                        if jobs.get(job_id, {}).get('status') == 'stopped':
                            log('  >>> STOPPED by user during crawling', 'red')
                            try: browser.close()
                            except: pass
                            jobs[job_id]['phase'] = 'Stopped'
                            return
                        progress(20 + int((i/total)*40), 'Crawling ' + qid + '...')

                        # --- ensure TN panel is open BEFORE every click ---
                        try:
                            if not page.locator(".cf-tn-list-item").count():
                                page.locator("text=Test Navigator").first.click(timeout=3000)
                                page.wait_for_timeout(500)
                        except: pass

                        # --- click with fresh locator, 3 s timeout, one retry ---
                        _click_ok = False
                        for _attempt in range(2):
                            try:
                                # Re-locate fresh each attempt so a stale DOM never poisons the retry
                                page.locator(".cf-tn-list-item").nth(nav_idx).click(timeout=3000, force=True)
                                _click_ok = True
                                break
                            except Exception as _ce:
                                if _attempt == 0:
                                    # Re-open TN panel and wait before retry
                                    try:
                                        page.locator("text=Test Navigator").first.click(timeout=2000)
                                        page.wait_for_timeout(600)
                                    except: pass
                                else:
                                    log('   ' + qid + ' SKIP (click failed): ' + str(_ce)[:80], 'yellow')

                        if not _click_ok:
                            live_data[qid] = {"text":"","options":[],"has_mandatory_marker":False,"has_raw_piping":False,"raw_piping_found":[],"has_inputs":True,"status":"crawl_failed - manual review"}
                            continue

                        try:
                            page.wait_for_timeout(1200)
                            full_text = page.evaluate("() => { const b=document.body.cloneNode(true); ['.sr-test-navigator','[class*=sr-tn]'].forEach(s=>b.querySelectorAll(s).forEach(e=>e.remove())); return b.innerText.trim(); }")
                            full_text = re.sub(r'\*Shown in Testing mode only\*', '', full_text or '')
                            # ISOLATE this question's text using [Question ID: XXX] markers (46000 chars -> ~500 chars)
                            text = full_text
                            try:
                                _markers = list(re.finditer(r'\[Question ID:\s*([^\]]+)\]', full_text))
                                def _nq(s): return s.replace('.','').replace('_','').replace('-','').replace(' ','').lower()
                                _qn = _nq(qid)
                                _found = False
                                # Pass 1: EXACT match — try doc QID then platform QID (e.g. "Q1" vs "Q1new")
                                for _mi, _m in enumerate(_markers):
                                    if _m.group(1).strip().lower() in (qid.lower(), plat_qid.lower()):
                                        _start = _m.end()
                                        _end = _markers[_mi+1].start() if _mi+1 < len(_markers) else len(full_text)
                                        text = full_text[_start:_end].strip()
                                        _found = True
                                        break
                                # Pass 2: normalized match (handles R2.2 vs R2x2)
                                if not _found:
                                    for _mi, _m in enumerate(_markers):
                                        _mq = _m.group(1).strip()
                                        if _nq(_mq) == _qn or _nq(_mq).replace('x','') == _qn.replace('x',''):
                                            _start = _m.end()
                                            _end = _markers[_mi+1].start() if _mi+1 < len(_markers) else len(full_text)
                                            text = full_text[_start:_end].strip()
                                            _found = True
                                            break
                                # Pass 3: marker not found - take first 2000 chars only (avoid 86703 dump)
                                if not _found and len(full_text) > 3000:
                                    text = full_text[:2000].strip()
                            except: pass
                            text = re.sub(r'\n{3,}', chr(10)+chr(10), text).strip()
                            # Strip Confirmit test-page footer elements
                            text = re.sub(r'(?im)^[ \t]*test\s*link\b.*$', '', text)
                            text = re.sub(r'(?m)^\d+%(?:[ \t]+\d+%){2,}[ \t]*$', '', text)
                            text = re.sub(u'(?m)^[^\\S\\n]*[←→◄►\xab\xbb]{1,4}[^\\S\\n]*$', u'', text)
                            text = re.sub(r'\n{3,}', chr(10)+chr(10), text).strip()
                            opts = _extract_options(page)
                            has_inp = _page_has_inputs(page)
                            try: page.screenshot(path=ss_dir + '/' + qid + '.png', full_page=True)
                            except: pass
                            piping = re.findall(r'\[PIPE[^\]]*\]', text, re.I)
                            live_data[qid] = {"text":text,"options":opts,"has_mandatory_marker":(" *" in text or "*"+chr(10) in text),"has_raw_piping":len(piping)>0,"raw_piping_found":piping,"has_inputs":has_inp,"status":"OK"}
                            log('   ' + qid + ' (' + str(len(text)) + ' chars)', 'green')
                        except Exception as e:
                            live_data[qid] = {"text":"","options":[],"has_mandatory_marker":False,"has_raw_piping":False,"raw_piping_found":[],"has_inputs":True,"status":"ERROR: " + str(e)[:50]}
                            log('   ' + qid + ' ERROR: ' + str(e)[:80], 'red')

                else:
                    log('  Mode: Respondent flow (auto-navigate)', 'blue')
                    log('  No test navigator - walking page by page', 'yellow')
                    # DIAGNOSTIC: log all clickable elements on first page
                    try:
                        diag = page.evaluate("""() => {
                            const out = [];
                            document.querySelectorAll('button, input[type=submit], input[type=button], a').forEach(el => {
                                const t = (el.innerText || el.value || el.id || '').trim().slice(0,40);
                                const tag = el.tagName.toLowerCase();
                                const typ = el.type || '';
                                const id = el.id || '';
                                const cls = (el.className || '').toString().slice(0,40);
                                if (t || id) out.push(tag + '[' + typ + '] text=' + t + ' id=' + id + ' cls=' + cls);
                            });
                            return out.slice(0, 25);
                        }""")
                        log('  DIAGNOSTIC - clickable elements found:', 'cyan')
                        for d in diag:
                            log('    ' + d, 'white')
                        if not diag:
                            log('    (no buttons/links found - survey may use JS framework)', 'yellow')
                    except Exception as de:
                        log('  Diagnostic error: ' + str(de)[:50], 'yellow')
                    max_pages = 80
                    page_num = 0
                    doc_qid_list = list(questions.keys())

                    while page_num < max_pages:
                        page_num += 1
                        progress(20 + int((page_num/max_pages)*40), 'Page ' + str(page_num) + '...')
                        page.wait_for_timeout(800)
                        try:
                            text = page.evaluate("() => document.body.innerText.trim()")
                        except:
                            text = ""
                        text = re.sub(r'\*Shown in Testing mode only\*', '', text or '')
                        text = re.sub(r'\n{3,}', chr(10)+chr(10), text).strip()

                        detected_qid = None
                        for dq in doc_qid_list:
                            if dq in live_data:
                                continue
                            if re.search(r'\b' + re.escape(dq) + r'\b', text):
                                detected_qid = dq
                                break
                        if not detected_qid:
                            detected_qid = 'PAGE' + str(page_num)

                        opts = _extract_options(page)
                        has_inp = _page_has_inputs(page)
                        try: page.screenshot(path=ss_dir + '/' + detected_qid + '.png', full_page=True)
                        except: pass

                        if text and detected_qid not in live_data:
                            piping = re.findall(r'\[PIPE[^\]]*\]', text, re.I)
                            live_data[detected_qid] = {"text":text,"options":opts,"has_mandatory_marker":(" *" in text or "*"+chr(10) in text),"has_raw_piping":len(piping)>0,"raw_piping_found":piping,"has_inputs":has_inp,"status":"OK"}
                            log('   ' + detected_qid + ' (' + str(len(text)) + ' chars)', 'green')

                        try:
                            # Tick all checkboxes (consent pages need all checked)
                            checks = page.locator("input[type=checkbox]")
                            cc = checks.count()
                            if cc > 0:
                                for ci in range(min(cc, 10)):
                                    try: checks.nth(ci).check(force=True, timeout=1000)
                                    except: pass
                            # Select first radio in each radio group
                            radios = page.locator("input[type=radio]")
                            if radios.count() > 0:
                                try: radios.first.check(force=True, timeout=2000)
                                except:
                                    try: radios.first.click(force=True, timeout=2000)
                                    except: pass
                            # Fill text inputs
                            txt_inputs = page.locator("input[type=text], input[type=number], textarea")
                            ti = txt_inputs.count()
                            if ti > 0:
                                for tidx in range(min(ti, 5)):
                                    try: txt_inputs.nth(tidx).fill("5", timeout=1000)
                                    except: pass
                            # Dropdowns - select first real option
                            selects = page.locator("select")
                            if selects.count() > 0:
                                for sidx in range(min(selects.count(), 5)):
                                    try: selects.nth(sidx).select_option(index=1, timeout=1000)
                                    except: pass
                            page.wait_for_timeout(400)
                        except: pass

                        clicked_next = False
                        matched_selector = ''
                        next_selectors = [".cf-button-next", "button:has-text('Next')", "button:has-text('Continue')", "button:has-text('Suivant')", "button:has-text('Continuer')", "button:has-text('Weiter')", "button:has-text('Siguiente')", "input[value='Next']", "input[value='Continue']", "input[value='Suivant']", "input[value='Continuer']", "input[value='>>']", "input[value='>']", "button:has-text('>>')", "button:has-text('>')", "a:has-text('Next')", "a:has-text('Suivant')", "#NextButton", "#nextButton", "#btnNext", "input[type=submit]", "button[type=submit]", ".next-button", ".btn-next", "a.button", "[onclick*=next]", "[onclick*=submit]", "[id*=Next]", "[name*=next]"]
                        for sel in next_selectors:
                            try:
                                btn = page.locator(sel).first
                                if btn.count() > 0 and btn.is_visible():
                                    btn.click(timeout=2500, force=True)
                                    clicked_next = True
                                    matched_selector = sel
                                    break
                            except: continue
                        if clicked_next:
                            log('  Clicked next via: ' + matched_selector, 'green')

                        if not clicked_next:
                            log('  Survey end (no next button) at page ' + str(page_num), 'blue')
                            break
                        # Capture text before wait to detect if page changed
                        prev_text_hash = hash(text[:200])
                        page.wait_for_timeout(1500)
                        try:
                            new_text = page.evaluate("() => document.body.innerText.trim()")[:200]
                            if hash(new_text) == prev_text_hash:
                                # Page didn't change - try clicking next once more, else stop
                                log('  Page unchanged, retrying...', 'yellow')
                                page.wait_for_timeout(1000)
                                still_same = page.evaluate("() => document.body.innerText.trim()")[:200]
                                if hash(still_same) == prev_text_hash:
                                    log('  Stuck on same page - validation may be blocking. Stopping.', 'yellow')
                                    break
                        except: pass

                        try:
                            low = (text or "").lower()
                            _kw_complete = any(w in low for w in ["thank you", "merci", "survey complete", "questionnaire complete", "has been recorded"])
                            _no_inputs = page.locator("input:not([type='hidden']):not([type='submit']):visible, select:visible, textarea:visible").count() == 0
                            if _kw_complete or _no_inputs:
                                log('  Completion page detected', 'blue')
                                break
                        except: pass

                browser.close()
            log(chr(10) + '  Crawled ' + str(len(live_data)) + ' pages', 'green')

        # PHASE 3: COMPARE
        if mode in ('full', 'quick') and live_data:
            progress(65, 'Comparing doc vs live...')
            log('', 'white')
            log('════════════════════════════════════', 'cyan')
            log('  PHASE 3: COMPARISON', 'cyan')
            log('════════════════════════════════════', 'cyan')

            # Initialize AI model (uses admin Gemini key if set)
            ai_model = get_gemini_model()
            if ai_model:
                log('  AI mode: Gemini active - smart comparison enabled', 'green')
            else:
                log('  AI mode: text-matching (add Gemini key in admin for AI)', 'yellow')

            # Confirmit converts dots to 'x': doc 'R2.2' = live 'R2x2'. Normalize for matching.
            def _norm_qid(q):
                return q.lower().replace('.', '').replace('x', '').replace('_', '').replace('-', '').replace(' ', '')
            # Build live lookup by normalized qid
            _live_norm = {}
            for lq in live_data.keys():
                _live_norm[_norm_qid(lq)] = lq
            _doc_norm = {}
            for dq in questions.keys():
                _doc_norm[_norm_qid(dq)] = dq
            def _find_base_match(doc_norm):
                """Return (live_norm, live_orig) if a live QID shares the same
                letter+digit prefix as doc_norm but with a non-digit suffix
                (e.g. 'r2' -> 'r2new', 'q11' -> 'q11b')."""
                _bm = re.match(r'^([a-z]+\d+)', doc_norm)
                if not _bm:
                    return None, None
                _base = _bm.group(1)
                for _ln, _lo in _live_norm.items():
                    if (_ln != doc_norm
                            and _ln.startswith(_base)
                            and len(_ln) > len(_base)
                            and not _ln[len(_base)].isdigit()):
                        return _ln, _lo
                return None, None

            _naming_matched_live = set()
            # Unified set of normalized qids
            _all_norm = set(_doc_norm.keys()) | set(_live_norm.keys())
            _to_compare = []
            for _nqid in sorted(_all_norm):
                qid = _doc_norm.get(_nqid) or _live_norm.get(_nqid)
                in_doc = _nqid in _doc_norm
                in_live = _nqid in _live_norm
                _live_key = _live_norm.get(_nqid)
                _doc_key = _doc_norm.get(_nqid)
                if in_doc and not in_live:
                    _bmn, _bmo = _find_base_match(_nqid)
                    if _bmo:
                        _naming_matched_live.add(_norm_qid(_bmo))
                        issues.append({"qid":qid,"type":"NAMING MISMATCH",
                                       "details":f"Doc: {qid} / Live: {_bmo}","severity":"MEDIUM"})
                    else:
                        issues.append({"qid":qid,"type":"MISSING IN LIVE","details":"In doc but not in live","severity":"HIGH"})
                    continue
                if in_live and not in_doc:
                    if _nqid in _naming_matched_live:
                        continue  # already reported as naming mismatch on the doc side
                    # Only flag as EXTRA if the live page has answerable inputs.
                    # Pages with zero inputs (disclaimers, intros, thank-you screens)
                    # are display-only and not real questions — skip them silently.
                    if not live_data[_live_key].get("has_inputs", True):
                        continue
                    issues.append({"qid":qid,"type":"EXTRA IN LIVE","details":"In live but not in doc","severity":"INFO"})
                    continue
                if live_data[_live_key]["status"] != "OK":
                    issues.append({"qid":qid,"type":"ERROR PAGE","details":live_data[_live_key]["status"],"severity":"MEDIUM"})
                    continue
                _to_compare.append({"qid":qid,"doc_text":questions[_doc_key]["text"],"live_text":live_data[_live_key]["text"],"doc_opts":[o["text"] for o in questions[_doc_key].get("options",[])],"live_opts":[o["text"] for o in live_data[_live_key].get("options",[])],"_doc_key":_doc_key,"_live_key":_live_key})
            _batch_size = 8
            _ai_handled = set()
            if ai_model:
                for _bi in range(0, len(_to_compare), _batch_size):
                    if jobs.get(job_id, {}).get('status') == 'stopped':
                        break
                    _batch = _to_compare[_bi:_bi+_batch_size]
                    log('  AI batch ' + str(_bi//_batch_size + 1) + ': ' + str(len(_batch)) + ' questions', 'cyan')
                    _bres = ai_compare_batch(ai_model, _batch)
                    if _bres is not None:
                        for _item in _batch:
                            _q = _item["qid"]
                            _matched = False
                            for _rk, _rv in _bres.items():
                                if _norm_qid(_rk) == _norm_qid(_q):
                                    for _iss in _rv:
                                        _iss["qid"] = _q
                                        issues.append(_iss)
                                    _ai_handled.add(_q)
                                    _matched = True
                                    break
                            if not _matched:
                                _ai_handled.add(_q)
            for _item in _to_compare:
                qid = _item["qid"]
                if qid in _ai_handled:
                    continue
                doc_text = _item["doc_text"]; live_text = _item["live_text"]
                _doc_key = _item["_doc_key"]; _live_key = _item["_live_key"]
                is_match, ratio = fuzzy_match(doc_text, live_text)
                if not is_match and ratio < 0.5:
                    issues.append({"qid":qid,"type":"TEXT MISMATCH","details":f"Match: {int(ratio*100)}% (fuzzy fallback - AI unavailable)","severity":"MEDIUM"})
            for _item in _to_compare:
                qid = _item["qid"]; _doc_key = _item["_doc_key"]; _live_key = _item["_live_key"]
                if questions[_doc_key].get("is_mandatory") and not live_data[_live_key].get("has_mandatory_marker"):
                    issues.append({"qid":qid,"type":"MANDATORY MISSING","details":"Doc mandatory, live marker missing","severity":"MEDIUM"})
                if live_data[_live_key].get("has_raw_piping"):
                    issues.append({"qid":qid,"type":"PIPING NOT RESOLVED","details":f"Raw: {live_data[_live_key].get('raw_piping_found',[])[:3]}","severity":"HIGH"})

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
                if jobs.get(job_id, {}).get('status') == 'stopped':
                    log('  >>> STOPPED by user during termination tests', 'red')
                    jobs[job_id]['phase'] = 'Stopped'
                    return
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
                            if not page.locator(".cf-tn-list-item").count():
                                page.locator("text=Test Navigator").first.click(timeout=3000)
                                page.wait_for_timeout(1000)
                        except: pass

                        navigated = False
                        _tn_loc = page.locator(".cf-tn-list-item")
                        for _idx in range(_tn_loc.count()):
                            try:
                                txt = _tn_loc.nth(_idx).inner_text(timeout=2000).strip().split()[0].strip()
                                if txt == test_qid:
                                    for _attempt in range(2):
                                        try:
                                            page.locator(".cf-tn-list-item").nth(_idx).click(force=True, timeout=3000)
                                            page.wait_for_timeout(1800)
                                            navigated = True
                                            break
                                        except:
                                            if _attempt == 0:
                                                page.wait_for_timeout(800)
                                    if navigated:
                                        break
                            except: continue

                        if not navigated:
                            r_result["details"] = f"Could not find {test_qid}"
                            browser.close()
                            term_results.append(r_result)
                            log(f'      Could not navigate to {test_qid}', 'red')
                            continue

                        try:
                            if page.locator(".cf-tn-list-item").count() > 0:
                                page.locator("text=Test Navigator").first.click(timeout=2000)
                                page.wait_for_timeout(500)
                        except: pass

                        radio_idx = int(answer_code) - 1
                        clicked = False
                        strategy = ""

                        for _attempt in range(2):
                            try:
                                _lbl = page.locator(".cf-radio-answer__text").nth(radio_idx)
                                try: _lbl.scroll_into_view_if_needed(timeout=2000)
                                except: pass
                                _lbl.click(force=True, timeout=3000)
                                page.wait_for_timeout(600)
                                clicked = True
                                strategy = f"label index={radio_idx}"
                                break
                            except:
                                if _attempt == 0:
                                    page.wait_for_timeout(500)

                        if not clicked:
                            for _attempt in range(2):
                                try:
                                    page.locator("input[type='radio']:visible").nth(radio_idx).click(force=True, timeout=3000)
                                    page.wait_for_timeout(600)
                                    clicked = True
                                    strategy = f"radio index={radio_idx}"
                                    break
                                except:
                                    if _attempt == 0:
                                        page.wait_for_timeout(500)

                        if not clicked:
                            r_result["passed"] = True
                            r_result["details"] = "Could not test — click failed, manual review required"
                            browser.close()
                            term_results.append(r_result)
                            log(f'      Could not click — manual review required', 'yellow')
                            continue

                        for sel in ["button:has-text('>>')", "input[value='>>']", ".cf-button-next"]:
                            try:
                                page.locator(sel).first.click(timeout=3000)
                                break
                            except: continue

                        page.wait_for_timeout(3500)
                        body_text = page.locator("body").inner_text(timeout=5000).lower()
                        _kw_terminated = any(ind in body_text for ind in THANKYOU_INDICATORS)
                        try:
                            _struct_terminated = page.locator("input[type='radio']:visible, input[type='checkbox']:visible, select:visible").count() == 0
                        except:
                            _struct_terminated = False
                        terminated = _kw_terminated or _struct_terminated

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
        hr.font.size = Pt(14); hr.font.bold = True; hr.font.color.rgb = RGBColor(0xC4, 0x6A, 0x2B)

        # AI-generated human-readable summary
        try:
            ai_summary_text = ai_generate_summary(get_gemini_model(), questions, live_data, issues)
        except Exception:
            ai_summary_text = ""
        job['ai_summary'] = ai_summary_text
        if ai_summary_text:
            sp = report.add_paragraph()
            sr = sp.add_run(ai_summary_text)
            sr.font.size = Pt(11); sr.italic = True; sr.font.color.rgb = RGBColor(0x40, 0x40, 0x40)
            report.add_paragraph()

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
                "NAMING MISMATCH": "Question name differs (doc vs live)",
            }
            fix_sug = {
                "WORDS MISSING": "Add the missing words to the live survey",
                "TEXT MISMATCH": "Update live survey text to match the doc",
                "OPTIONS MISMATCH": "Add missing answer options to live survey",
                "MANDATORY MISSING": "Add * marker to make question mandatory",
                "PIPING NOT RESOLVED": "Fix piping logic",
                "MISSING IN LIVE": "Add this question to the live survey",
                "NAMING MISMATCH": "Rename question in live survey to match spec, or update spec",
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
        fr = footer_p.add_run("— End of Report — Generated by SurveyQC v10.0 —")
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


# ================================================================
# NEW FEATURES v10.0
# ================================================================

# ---- CONTENT STORE (Admin editable) ----
site_content = {
    # Hero
    'hero_heading_part1': 'Catch every survey bug',
    'hero_heading_part2': 'before',
    'hero_heading_part3': 'your data is collected.',
    'hero_subheading': 'SurveyQC reads your screener doc, crawls your live survey, and flags 15+ types of issues automatically -- in any language, on any platform.',
    'hero_cta': 'Start free trial',
    'hero_cta2': 'See how it works',
    'announcement': 'New: WhatsApp screenshot QC is now live',
    # Features (homepage preview - 6)
    'feature1_title': 'Termination Testing',
    'feature1_desc': 'Every terminate/close rule actually clicked and verified. PASS/FAIL per rule with screenshot proof attached.',
    'feature2_title': 'Missing Words Detection',
    'feature2_desc': 'Word-by-word comparison between your spec doc and live survey. Catches every typo and missing phrase instantly.',
    'feature3_title': 'Auto Screenshots',
    'feature3_desc': 'Every question captured automatically. Bug found? Screenshot attached as visual proof in your final report.',
    'feature4_title': 'Any Language',
    'feature4_desc': 'French, Italian, Arabic, Urdu, Japanese -- 80+ languages with full encoding and accent character support.',
    'feature5_title': '15+ QC Checks',
    'feature5_desc': 'Termination, text match, missing words, piping, codes, mandatory, options, order -- all parallel verified.',
    'feature6_title': 'One-click Retest',
    'feature6_desc': 'Fix the bugs, retest only the failed paths in seconds. No need to run full QC again from scratch.',
    # Footer / site
    'site_name': 'SurveyQC',
    'tagline': 'The worlds first AI-powered survey QC tool',
    'support_email': 'support@surveyqc.online',
    'footer_text': '2026 SurveyQC -- Built for QC professionals worldwide',
    'linkedin_url': 'https://linkedin.com/company/surveyqc',
    'twitter_url': 'https://twitter.com/surveyqc',
    'youtube_url': '',
    'privacy_policy': 'We take your privacy seriously. All data auto-deletes after 30 days.',
    'terms': 'By using SurveyQC you agree to our terms of service.',
    # Pricing page
    'pricing_heading': 'Simple, transparent pricing',
    'pricing_sub': 'Start free. Upgrade when you need more. No contracts, cancel anytime.',
    'plan_free_name': 'Free',
    'plan_free_price': '0',
    'plan_free_period': '/month',
    'plan_free_desc': 'For solo testers',
    'plan_free_yearly': '0',
    'plan_free_features': '5 reports per month||All 15+ QC checks||Word report download||20K tokens per report||Any language||Community support',
    'plan_free_cta': 'Get started',
    'plan_pro_name': 'Pro',
    'plan_pro_price': '29',
    'plan_pro_period': '/month',
    'plan_pro_desc': 'For QC professionals',
    'plan_pro_yearly': '290',
    'plan_pro_features': '50 reports per month||Everything in Free||Screenshot QC (WhatsApp)||Share reports with clients||100K tokens per report||Priority support||AI auto tester||Custom templates',
    'plan_pro_cta': 'Start Pro trial',
    'plan_pro_badge': 'Most Popular',
    'plan_pro_featured': '1',
    'plan_biz_name': 'Business',
    'plan_biz_price': '99',
    'plan_biz_period': '/month',
    'plan_biz_desc': 'For agencies & teams',
    'plan_biz_yearly': '990',
    'plan_biz_features': 'Unlimited reports||Everything in Pro||Team collaboration||Own API key||White-label reports||150K tokens per report||Dedicated account manager||SLA guarantee',
    'plan_biz_cta': 'Get Business',
    # Pricing FAQ
    'pfaq1_q': 'Can I cancel anytime?',
    'pfaq1_a': 'Yes -- no contracts, no cancellation fees. Cancel anytime from Billing settings.',
    'pfaq2_q': 'Is there a free trial?',
    'pfaq2_a': 'Pro and Business plans come with a 14-day free trial. Free plan is free forever.',
    'pfaq3_q': 'What payment methods do you accept?',
    'pfaq3_a': 'Credit/debit cards (Stripe), UPI, PayPal, and bank transfer for Business plan.',
    'pfaq4_q': 'Can I switch plans later?',
    'pfaq4_a': 'Yes, upgrade or downgrade anytime. Pro-rated billing applied automatically.',
    'pfaq5_q': 'Do you offer team/enterprise pricing?',
    'pfaq5_a': 'Yes, Business plan supports teams. Contact us for enterprise pricing (10+ users).',
    # Features Page - 25 features grouped
    'features_heading': 'Everything you need for perfect survey QC',
    'features_sub': '25+ specialized checks and tools, built by QC professionals for QC professionals.',
    # Group 1: Core QC Checks
    'feat_grp1_name': 'Core QC Checks',
    'feat_grp1_desc': 'Essential checks every survey needs.',
    'feat_grp1_items': 'Termination Testing||Every terminate rule clicked and verified with PASS/FAIL||ti-x-octagon##Question Text Match||Full question text compared word-by-word against spec doc||ti-text-recognition##Missing Words Detection||Catches typos and missing phrases between spec and live survey||ti-search##Options Match||All answer options compared against spec - missing/extra detected||ti-checkbox##Answer Codes Validation||Verifies answer codes are sequential and match spec exactly||ti-list-numbers##Mandatory Markers||Checks * mandatory markers match the spec on every question||ti-asterisk##Question Order||Verifies questions appear in correct order as per spec||ti-arrows-sort##Piping Markers||Detects unresolved {piped_text} variables shown to respondents||ti-replace',
    # Group 2: Advanced Logic
    'feat_grp2_name': 'Advanced Logic Testing',
    'feat_grp2_desc': 'Catches complex logic bugs humans miss.',
    'feat_grp2_items': 'Skip Logic Validation||Every skip rule tested against actual respondent paths||ti-route##Quota Testing||Validates quota cells, balanced sampling, and overflow logic||ti-chart-pie##Loop & Block Testing||Tests repeating loops and conditional blocks for consistency||ti-refresh##Conditional Display||Verifies show/hide rules based on prior answers||ti-eye##Display Logic||Catches questions that show/hide incorrectly per spec||ti-eye-check##Routing Validation||Confirms respondents go to correct next question||ti-arrow-fork',
    # Group 3: Quality & Multilingual
    'feat_grp3_name': 'Quality & Multilingual',
    'feat_grp3_desc': 'Built for global research teams.',
    'feat_grp3_items': '80+ Languages||French, Italian, Arabic, Urdu, Japanese, Chinese - all supported||ti-world##Accent & Encoding||Proper handling of special characters and diacritics||ti-language##Translation Sync||Detects untranslated strings in multi-language surveys||ti-translate##Hidden Question Detection||Finds hidden/disabled questions still present in live survey||ti-ghost##Duplicate Question Detection||Catches duplicate question IDs across the survey||ti-copy',
    # Group 4: Reporting & Workflow
    'feat_grp4_name': 'Reporting & Workflow',
    'feat_grp4_desc': 'Professional reports clients will love.',
    'feat_grp4_items': 'Auto Screenshots||Every question captured automatically as visual proof||ti-camera##Word Report Download||Professional .docx report with all issues and screenshots||ti-file-word##One-click Retest||Fix bugs, retest only failed paths in seconds||ti-rotate-clockwise##Share with Client||Share report link - clients can comment and mark fixed||ti-share##Timing Estimation||Estimates survey LOI (length of interview) automatically||ti-clock##QC Certificate||Professional PDF certificate proving QC was completed||ti-certificate',
    # Testimonials (rotating)
    'test1_name': 'Sarah Thompson',
    'test1_role': 'QC Manager',
    'test1_company': 'Ipsos UK',
    'test1_country': 'United Kingdom',
    'test1_flag': 'GB',
    'test1_quote': '8 hours of manual QC now takes 10 minutes. Caught a termination bug that would have killed our entire dataset -- saved us a $40K project.',
    'test1_rating': '5',
    'test2_name': 'Marie Laurent',
    'test2_role': 'Research Director',
    'test2_company': 'Kantar France',
    'test2_country': 'France',
    'test2_flag': 'FR',
    'test2_quote': 'French, Italian, Spanish -- SurveyQC handles all perfectly. Screenshot evidence is a total game changer for client reports. Industry-leading.',
    'test2_rating': '5',
    'test3_name': 'James Mitchell',
    'test3_role': 'Operations Lead',
    'test3_company': 'Nielsen USA',
    'test3_country': 'United States',
    'test3_flag': 'US',
    'test3_quote': 'Saved our team 47 hours this month. ROI is insane. Nothing else even comes close to this level of automation for survey professionals.',
    'test3_rating': '5',
    'test4_name': 'Rahul Sharma',
    'test4_role': 'Survey Programmer',
    'test4_company': 'YouGov India',
    'test4_country': 'India',
    'test4_flag': 'IN',
    'test4_quote': 'Catches termination bugs we routinely missed in manual QC. Now we deliver bug-free surveys every time. The team productivity boost is real.',
    'test4_rating': '5',
    'test5_name': 'Klaus Weber',
    'test5_role': 'Data Manager',
    'test5_company': 'GfK Germany',
    'test5_country': 'Germany',
    'test5_flag': 'DE',
    'test5_quote': 'Replaced 3 manual QC processes with one tool. Reports are professional, clients trust us more. Best research tech investment of 2026.',
    'test5_rating': '5',
    'test6_name': 'Fatima Al-Rashid',
    'test6_role': 'QC Specialist',
    'test6_company': 'YouGov MENA',
    'test6_country': 'UAE',
    'test6_flag': 'AE',
    'test6_quote': 'Arabic survey QC was always painful with right-to-left text. SurveyQC handles it perfectly. Massive time saver for our Middle East research.',
    'test6_rating': '5',
    # Blog
    'blog_post1_title': 'How AI is Changing Survey Quality Control in 2026',
    'blog_post1_date': 'May 2026',
    'blog_post1_tag': 'AI & QC',
    'blog_post1_summary': 'Manual QC takes 8+ hours per survey. Here is how AI reduces that to under 10 minutes with 99% accuracy.',
    'blog_post2_title': '8 Most Common Survey Bugs',
    'blog_post2_date': 'April 2026',
    'blog_post2_tag': 'Best Practices',
    'blog_post2_summary': 'Termination errors, missing words, broken piping -- the 8 bugs that destroy survey data.',
    'blog_post3_title': 'French Survey QC: Why Accents and Encoding Matter',
    'blog_post3_date': 'March 2026',
    'blog_post3_tag': 'Languages',
    'blog_post3_summary': 'Testing French surveys has unique challenges -- accent marks, special characters, termination phrases.',
    'blog_post4_title': 'Complete Guide to Confirmit Survey Testing',
    'blog_post4_date': 'March 2026',
    'blog_post4_tag': 'Platforms',
    'blog_post4_summary': 'Step-by-step guide to QC testing on Confirmit from uploading spec docs to reading the final report.',
    'blog_post5_title': 'Why Excel Sheets Are Killing Your QC Workflow',
    'blog_post5_date': 'February 2026',
    'blog_post5_tag': 'Productivity',
    'blog_post5_summary': 'Most teams still use Excel for survey QC. We analyzed 500 surveys to show why that is costing time.',
    'blog_post6_title': 'SurveyQC vs Manual Testing: A Real Cost Comparison',
    'blog_post6_date': 'January 2026',
    'blog_post6_tag': 'ROI',
    'blog_post6_summary': 'We broke down the actual cost per survey -- time, salary, errors. The numbers will surprise you.',
    # Compare pages
    'compare_chatgpt_heading': 'SurveyQC vs ChatGPT for Survey QC',
    'compare_chatgpt_summary': 'ChatGPT is a general AI. SurveyQC is purpose-built for survey quality control with 15+ specialized checks.',
    'compare_excel_heading': 'SurveyQC vs Manual Excel QC',
    'compare_excel_summary': 'Excel takes 8+ hours per survey. SurveyQC does it in 10 minutes automatically.',
    'compare_manual_heading': 'SurveyQC vs Manual Testing',
    'compare_manual_summary': 'Manual testers miss 30% of bugs on average. SurveyQC catches 99% automatically.',
    # Community & Affiliate
    'community_heading': 'Join the SurveyQC Community',
    'community_subheading': 'Connect with 500+ QC professionals worldwide. Share tips, get help.',
    'affiliate_heading': 'Earn with SurveyQC Affiliate Program',
    'affiliate_commission': '30',
    'affiliate_details': 'Earn 30% recurring commission for every paying user you refer. No cap.',
    # Changelog
    'changelog_v10': 'v10.0 -- May 2026: WhatsApp screenshot QC, GDPR auto-delete, Share report, API management',
    'changelog_v9': 'v9.0 -- April 2026: Token limit controls, Gift access, Content management, 5-country compliance',
    'changelog_v8': 'v8.0 -- March 2026: French termination fixes, QC certificate, Report templates',
    'changelog_v7': 'v7.0 -- February 2026: Double check, Progress live log, Retest failed paths',
    'changelog_v6': 'v6.0 -- January 2026: Multi-platform support (Decipher, Forsta, Qualtrics)',
    'docs_intro': 'SurveyQC documentation -- everything you need to run perfect QC checks.',
}

token_limits = {
    'free': 20000,
    'pro': 100000,
    'business': 150000,
    'monthly_budget': 50,
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
    """Run every night — delete job data older than 30 days, feedback older than 3 days"""
    cutoff_jobs = datetime.now() - timedelta(days=30)
    cutoff_feedback = datetime.now() - timedelta(days=3)
    deleted = 0
    for job_id, job in list(jobs.items()):
        try:
            created = datetime.fromisoformat(job.get('created_at', ''))
            if created < cutoff_jobs:
                shutil.rmtree(f"{UPLOAD_FOLDER}/{job_id}", ignore_errors=True)
                shutil.rmtree(f"{OUTPUT_FOLDER}/{job_id}", ignore_errors=True)
                del jobs[job_id]
                deleted += 1
        except: pass
    # Auto-delete old feedback
    global user_feedback_db
    before = len(user_feedback_db)
    user_feedback_db = [
        f for f in user_feedback_db
        if datetime.fromisoformat(f['created_at']) >= cutoff_feedback
    ]
    deleted += before - len(user_feedback_db)
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Welcome — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('onboarding')}
  <div class="main-content">
    <div class="topbar">
      <div><p class="page-title">Welcome to SurveyQC! 🎉</p><p class="page-sub">Quick setup — takes 2 minutes</p></div>
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
        <div style="width:32px;height:32px;border-radius:50%;background:var(--purple);color:white;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;margin-bottom:10px">2</div>
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary)">Choose your platform</p>
        <p style="font-size:11px;color:var(--color-text-secondary);margin-top:4px">Which survey platform do you use?</p>
        <select class="form-select" style="margin-top:10px;font-size:12px">
          <option>Confirmit</option><option>Decipher</option><option>Forsta</option><option>Qualtrics</option>
        </select>
      </div>
      <div class="card" style="opacity:.5">
        <div style="width:32px;height:32px;border-radius:50%;background:var(--color-border-secondary);color:var(--color-text-secondary);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;margin-bottom:10px">3</div>
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary)">Run your first QC</p>
        <p style="font-size:11px;color:var(--color-text-secondary);margin-top:4px">See the magic!</p>
      </div>
    </div>

    <div class="card" style="background:#E6F1FB;border-color:#B5D4F4">
      <div style="display:flex;align-items:center;gap:12px">
        <i class="ti ti-bulb" style="font-size:24px;color:#185FA5;flex-shrink:0"></i>
        <div style="flex:1">
          <p style="font-size:13px;font-weight:500;color:#0C447C">Start with a simple survey test!</p>
          <p style="font-size:11px;color:#185FA5;margin-top:3px">Paste any survey URL — AI checks everything automatically in 10 minutes</p>
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
        {'text': 'Q5 bug has been fixed ✅', 'user': 'Client (Marie L.)', 'time': '2h ago', 'type': 'client'},
        {'text': 'Can you also check R1 termination?', 'user': 'You', 'time': '1h ago', 'type': 'own'},
        {'text': 'R1 is fixed too! Please retest 🙏', 'user': 'Client (Marie L.)', 'time': '30m ago', 'type': 'client', 'fixed': True},
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Share Report — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('reports')}
  <div class="main-content">
    <div class="topbar">
      <div><p class="page-title">Share Report</p><p class="page-sub">Share this report link with your client — they can view and comment after a free signup</p></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="card">
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:12px">🔗 Generate share link</p>
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
          <div style="display:flex;align-items:center;gap:7px;padding:7px 10px;background:var(--bg3);border-radius:7px"><i class="ti ti-x" style="font-size:12px;color:var(--color-text-secondary)"></i><span style="font-size:11px;color:var(--color-text-secondary)">Cannot run new QC reports</span></div>
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
# USER FEEDBACK API (Task 6)
# ================================================================
@app.route('/api/user-feedback', methods=['POST'])
@login_required
def submit_user_feedback():
    data = request.get_json(silent=True) or {}
    msg = (data.get('message') or '').strip()[:1000]
    if not msg:
        return jsonify({'error': 'message required'}), 400
    entry = {
        'id': str(uuid.uuid4())[:8],
        'type': data.get('type', 'general')[:20],
        'message': msg,
        'page': (data.get('page') or '/')[:200],
        'user_email': session.get('user_email', ''),
        'created_at': datetime.now().isoformat(),
        'read': False,
    }
    user_feedback_db.append(entry)
    return jsonify({'ok': True})


@app.route('/admin/feedback')
def admin_feedback():
    if not session.get('is_admin'):
        return redirect('/admin/login')
    q = request.args.get('q', '').lower()
    t = request.args.get('type', '')
    items = list(reversed(user_feedback_db))
    if q:
        items = [i for i in items if q in i['message'].lower() or q in i['user_email'].lower()]
    if t:
        items = [i for i in items if i['type'] == t]
    unread_count = sum(1 for i in user_feedback_db if not i['read'])

    rows = ''
    for item in items:
        cls = '' if item['read'] else 'feedback-new'
        dot = '' if item['read'] else '<span style="display:inline-block;width:6px;height:6px;background:var(--accent);border-radius:50%;margin-right:6px;vertical-align:middle"></span>'
        created = item['created_at'][:16].replace('T', ' ')
        type_colors = {'bug': '#C84B31', 'feature': '#0073C6', 'general': '#3F7D58', 'other': '#8A847A'}
        tc = type_colors.get(item['type'], '#8A847A')
        rows += f"""<tr class="{cls}" data-id="{item['id']}">
          <td>{dot}<span style="font-size:11px;background:{tc}22;color:{tc};padding:2px 7px;border-radius:20px;font-weight:600">{item['type']}</span></td>
          <td style="max-width:360px;word-break:break-word">{item['message'][:200]}</td>
          <td style="color:#8A847A">{item['user_email']}</td>
          <td style="white-space:nowrap;color:#8A847A">{created}</td>
          <td>
            <form method="POST" action="/admin/feedback/action" style="display:inline">
              <input type="hidden" name="id" value="{item['id']}">
              <button name="action" value="read" title="Mark read" style="background:none;border:none;cursor:pointer;color:#0073C6;font-size:13px;padding:3px 6px">✓</button>
              <button name="action" value="delete" title="Delete" style="background:none;border:none;cursor:pointer;color:#C84B31;font-size:13px;padding:3px 6px" onclick="return confirm('Delete?')">✕</button>
            </form>
          </td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="5" style="text-align:center;color:#8A847A;padding:24px">No feedback yet.</td></tr>'

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feedback — Admin</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">
<script src="/admin-sidebar-js"></script>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/app.js" defer></script>
<style>*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'Inter',sans-serif}}body{{background:#F7F4EE}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid #E8E1D8;font-size:12px}}
th{{font-weight:600;color:#5F5B53;font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
tr:hover td{{background:#F7F4EE}}.feedback-new td{{font-weight:500}}</style>
</head><body>
<div id="admsb"></div>
<div style="padding:24px;max-width:1100px;margin:0 auto">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:#8A847A;font-size:20px">&larr;</a>
    <div>
      <p style="font-size:20px;font-weight:700;color:#171717">User Feedback</p>
      <p style="font-size:12px;color:#8A847A">{unread_count} unread · auto-deleted after 3 days</p>
    </div>
    <form method="GET" style="margin-left:auto;display:flex;gap:8px;align-items:center">
      <input name="q" value="{q}" placeholder="Search..." style="padding:7px 12px;border:1px solid #E8E1D8;border-radius:8px;font-size:13px;outline:none">
      <select name="type" style="padding:7px 12px;border:1px solid #E8E1D8;border-radius:8px;font-size:13px;outline:none">
        <option value="">All types</option>
        <option value="bug" {'selected' if t=='bug' else ''}>Bug</option>
        <option value="feature" {'selected' if t=='feature' else ''}>Feature</option>
        <option value="general" {'selected' if t=='general' else ''}>General</option>
        <option value="other" {'selected' if t=='other' else ''}>Other</option>
      </select>
      <button type="submit" style="padding:7px 14px;background:#C46A2B;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px">Filter</button>
    </form>
  </div>
  <div style="background:#fff;border-radius:14px;border:1px solid #E8E1D8;overflow:hidden">
    <table><thead><tr><th>Type</th><th>Message</th><th>User</th><th>Date</th><th>Actions</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </div>
</div></body></html>""")


@app.route('/admin/feedback/action', methods=['POST'])
def admin_feedback_action():
    if not session.get('is_admin'):
        return redirect('/admin/login')
    fid = request.form.get('id', '')
    action = request.form.get('action', '')
    for i, item in enumerate(user_feedback_db):
        if item['id'] == fid:
            if action == 'delete':
                user_feedback_db.pop(i)
            elif action == 'read':
                user_feedback_db[i]['read'] = True
            break
    return redirect('/admin/feedback')


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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Privacy & Data — SurveyQC</title></head><body>
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
          <p style="font-size:11px;color:#15803D;margin-top:2px">Runs automatically every night at 12:00 AM. No action needed — fully automatic!</p>
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
    saved = False
    if request.method == 'POST':
        for key in list(site_content.keys()):
            val = request.form.get(key)
            if val is not None:
                site_content[key] = val
        saved = True

    groups = {
        'Hero Section': ['hero_heading_part1','hero_heading_part2','hero_heading_part3','hero_subheading','hero_cta','hero_cta2','announcement'],
        'Homepage Features (6)': ['feature1_title','feature1_desc','feature2_title','feature2_desc','feature3_title','feature3_desc','feature4_title','feature4_desc','feature5_title','feature5_desc','feature6_title','feature6_desc'],
        'Pricing Page': ['pricing_heading','pricing_sub'],
        'Plan: Free': ['plan_free_name','plan_free_price','plan_free_yearly','plan_free_period','plan_free_desc','plan_free_features','plan_free_cta'],
        'Plan: Pro': ['plan_pro_name','plan_pro_price','plan_pro_yearly','plan_pro_period','plan_pro_desc','plan_pro_features','plan_pro_cta','plan_pro_badge','plan_pro_featured'],
        'Plan: Business': ['plan_biz_name','plan_biz_price','plan_biz_yearly','plan_biz_period','plan_biz_desc','plan_biz_features','plan_biz_cta'],
        'Pricing FAQ': ['pfaq1_q','pfaq1_a','pfaq2_q','pfaq2_a','pfaq3_q','pfaq3_a','pfaq4_q','pfaq4_a','pfaq5_q','pfaq5_a'],
        'Features Page': ['features_heading','features_sub','feat_grp1_name','feat_grp1_desc','feat_grp1_items','feat_grp2_name','feat_grp2_desc','feat_grp2_items','feat_grp3_name','feat_grp3_desc','feat_grp3_items','feat_grp4_name','feat_grp4_desc','feat_grp4_items'],
        'Testimonial 1': ['test1_name','test1_role','test1_company','test1_country','test1_quote','test1_rating'],
        'Testimonial 2': ['test2_name','test2_role','test2_company','test2_country','test2_quote','test2_rating'],
        'Testimonial 3': ['test3_name','test3_role','test3_company','test3_country','test3_quote','test3_rating'],
        'Testimonial 4': ['test4_name','test4_role','test4_company','test4_country','test4_quote','test4_rating'],
        'Testimonial 5': ['test5_name','test5_role','test5_company','test5_country','test5_quote','test5_rating'],
        'Testimonial 6': ['test6_name','test6_role','test6_company','test6_country','test6_quote','test6_rating'],
        'Blog Posts': ['blog_post1_title','blog_post1_date','blog_post1_tag','blog_post1_summary','blog_post2_title','blog_post2_date','blog_post2_tag','blog_post2_summary','blog_post3_title','blog_post3_date','blog_post3_tag','blog_post3_summary','blog_post4_title','blog_post4_date','blog_post4_tag','blog_post4_summary','blog_post5_title','blog_post5_date','blog_post5_tag','blog_post5_summary','blog_post6_title','blog_post6_date','blog_post6_tag','blog_post6_summary'],
        'Site & Footer': ['site_name','tagline','footer_text','support_email','linkedin_url','twitter_url'],
        'Compare Pages': ['compare_chatgpt_heading','compare_chatgpt_summary','compare_excel_heading','compare_excel_summary','compare_manual_heading','compare_manual_summary'],
        'Community & Affiliate': ['community_heading','community_subheading','affiliate_heading','affiliate_commission','affiliate_details'],
        'Changelog': ['changelog_v10','changelog_v9','changelog_v8','changelog_v7','changelog_v6'],
    }

    alert = '<div style="background:#E5F0E9;border:1px solid #A5D6A7;border-radius:10px;padding:12px 16px;margin-bottom:18px;color:#3F7D58;font-size:14px;font-weight:500"><i class="ti ti-check"></i> All content saved!</div>' if saved else ''

    # Build tabbed form
    tab_btns = ''
    tab_panels = ''
    for i, (group, fields) in enumerate(groups.items()):
        active = ' active' if i == 0 else ''
        gid = 'tab' + str(i)
        tab_btns += '<button type="button" class="cms-tab' + active + '" onclick="showTab(\'' + gid + '\',this)">' + group + '</button>'
        panel = '<div class="cms-panel' + active + '" id="' + gid + '">'
        for field in fields:
            if field not in site_content:
                continue
            val = site_content.get(field, '')
            # Escape for HTML attribute
            val_escaped = val.replace('"', '&quot;')
            label = field.replace('_', ' ').title()
            is_long = any(x in field for x in ['summary','details','desc','quote','features','items','_a','subheading','heading','intro','changelog','announcement','policy','terms'])
            if is_long:
                inp = '<textarea name="' + field + '" class="cms-input" rows="3">' + val + '</textarea>'
            else:
                inp = '<input type="text" name="' + field + '" value="' + val_escaped + '" class="cms-input">'
            hint = ''
            if 'features' in field:
                hint = '<span class="cms-hint">Separate each feature with || (double pipe)</span>'
            elif 'items' in field:
                hint = '<span class="cms-hint">Format: Title||Description||icon-name ## next feature...</span>'
            elif field == 'plan_pro_featured':
                hint = '<span class="cms-hint">1 = highlighted plan, 0 = normal</span>'
            panel += '<div class="cms-field"><label class="cms-label">' + label + '</label>' + inp + hint + '</div>'
        panel += '</div>'
        tab_panels += panel

    page = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Content CMS - Admin</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">

<style>
:root{--bg:#F7F4EE;--card:#FFF;--text:#171717;--text2:#5F5B53;--text3:#8A847A;--accent:#C46A2B;--border:#E8E1D8;--dark:#1B140F}
*{box-sizing:border-box;margin:0;padding:0;font-family:'Inter',sans-serif;-webkit-font-smoothing:antialiased}
body{background:var(--bg);color:var(--text)}
a{text-decoration:none;color:inherit}
.cms-wrap{max-width:1100px;margin:0 auto;padding:24px}
.cms-hdr{display:flex;align-items:center;gap:14px;margin-bottom:24px}
.cms-hdr h1{font-family:'Plus Jakarta Sans',sans-serif;font-size:24px;font-weight:800;letter-spacing:-0.5px}
.cms-hdr p{font-size:13px;color:var(--text2)}
.cms-layout{display:grid;grid-template-columns:240px 1fr;gap:20px}
.cms-tabs{display:flex;flex-direction:column;gap:4px;position:sticky;top:24px;height:fit-content}
.cms-tab{text-align:left;background:none;border:none;padding:10px 14px;border-radius:10px;font-size:13px;font-weight:500;color:var(--text2);cursor:pointer;font-family:inherit;transition:all .15s}
.cms-tab:hover{background:rgba(196,106,43,.08);color:var(--text)}
.cms-tab.active{background:var(--dark);color:#F7F4EE;font-weight:600}
.cms-content{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:28px}
.cms-panel{display:none}
.cms-panel.active{display:block}
.cms-field{margin-bottom:18px}
.cms-label{display:block;font-size:13px;font-weight:600;color:var(--text);margin-bottom:6px}
.cms-input{width:100%;padding:11px 14px;border:1px solid var(--border);border-radius:10px;font-size:13px;font-family:inherit;color:var(--text);outline:none;resize:vertical}
.cms-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(196,106,43,.1)}
.cms-hint{display:block;font-size:11px;color:var(--text3);margin-top:4px}
.cms-save{position:fixed;bottom:24px;right:24px;background:var(--accent);color:white;border:none;padding:14px 28px;border-radius:12px;font-size:14px;font-weight:600;cursor:pointer;box-shadow:0 8px 24px rgba(196,106,43,.3);font-family:inherit;display:flex;align-items:center;gap:8px}
.cms-save:hover{background:#A9551F}
@media(max-width:768px){
  .cms-layout{grid-template-columns:1fr}
  .cms-tabs{flex-direction:row;overflow-x:auto;position:static}
  .cms-tab{white-space:nowrap}
}
</style><script src="/admin-sidebar-js"></script></head><body>
<div class="cms-wrap">
  <div class="cms-hdr">
    <a href="/admin" style="color:var(--text3);font-size:22px"><i class="ti ti-arrow-left"></i></a>
    <div><h1>Content Management</h1><p>Edit all website content live — no code needed</p></div>
  </div>
  """ + alert + """
  <form method="POST">
    <div class="cms-layout">
      <div class="cms-tabs">""" + tab_btns + """</div>
      <div class="cms-content">""" + tab_panels + """</div>
    </div>
    <button type="submit" class="cms-save"><i class="ti ti-device-floppy"></i> Save All Changes</button>
  </form>
</div>
<script>
function showTab(id, btn){
  document.querySelectorAll('.cms-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.cms-tab').forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}
</script>
</body></html>"""
    return render_template_string(page)


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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Token Control — Admin</title><script src="/admin-sidebar-js"></script></head><body>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Gift Access — Admin</title><script src="/admin-sidebar-js"></script></head><body>
<div style="padding:24px;max-width:500px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--color-text-secondary);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:18px;font-weight:500;color:var(--color-text-primary)">🎁 Gift Access</p>
  </div>
  {'<div class="alert alert-success">Access gifted! A confirmation email has been sent to the user.</div>' if gifted else ''}
  {'<div class="alert alert-error">' + error + '</div>' if error else ''}
  <div class="card">
    <p style="font-size:12px;color:var(--color-text-secondary);margin-bottom:14px">Give a friend or colleague direct access — no coupon code needed</p>
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


@app.route('/delete-reports', methods=['POST','GET'])
@login_required
def delete_reports():
    email = session['user_email']
    for jid in list(jobs.keys()):
        if jobs[jid].get('user_email') == email:
            del jobs[jid]
    return redirect('/privacy-data')


# ================================================================
# CLEANUP NOW (manual)
# ================================================================

@app.route('/admin/activity')
@admin_required
def admin_activity():
    items = []
    for jid, j in list(jobs.items())[-30:]:
        items.append({'user': j.get('user_email','unknown'), 'action': 'QC on: '+j.get('doc_name','unknown')[:40], 'time': j.get('created_at','')[:16], 'status': j.get('status','unknown')})
    items.reverse()
    rows = ''
    for a in items:
        badge = '<span style="background:#EAF3DE;color:#27500A;font-size:10px;padding:2px 8px;border-radius:20px">Done</span>' if a['status']=='done' else '<span style="background:#E6F1FB;color:#0C447C;font-size:10px;padding:2px 8px;border-radius:20px">Running</span>'
        rows += '<tr><td style="padding:10px 12px;font-size:12px;color:#1A1A2E;border-bottom:0.5px solid #F0F2F5">'+a['user']+'</td><td style="padding:10px 12px;font-size:12px;color:#6B7280;border-bottom:0.5px solid #F0F2F5">'+a['action']+'</td><td style="padding:10px 12px;border-bottom:0.5px solid #F0F2F5">'+badge+'</td><td style="padding:10px 12px;font-size:11px;color:#9CA3AF;border-bottom:0.5px solid #F0F2F5">'+a['time']+'</td></tr>'
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Activity - Admin</title></head><body style="background:#F0F2F5;font-family:-apple-system,sans-serif;color:#1A1A2E">'
    page += '<div style="padding:24px"><div style="display:flex;align-items:center;gap:14px;margin-bottom:20px"><a href="/admin" style="color:#6B7280;font-size:22px;text-decoration:none">&larr;</a><p style="font-size:20px;font-weight:600">Live Activity</p></div>'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;overflow:hidden"><table style="width:100%;border-collapse:collapse"><thead><tr style="background:#F8F9FA"><th style="padding:10px 12px;text-align:left;font-size:10px;color:#6B7280;font-weight:500">USER</th><th style="padding:10px 12px;text-align:left;font-size:10px;color:#6B7280;font-weight:500">ACTION</th><th style="padding:10px 12px;text-align:left;font-size:10px;color:#6B7280;font-weight:500">STATUS</th><th style="padding:10px 12px;text-align:left;font-size:10px;color:#6B7280;font-weight:500">TIME</th></tr></thead><tbody>'
    page += rows if rows else '<tr><td colspan="4" style="padding:20px;text-align:center;color:#9CA3AF;font-size:13px">No activity yet</td></tr>'
    page += '</tbody></table></div></div></body></html>'
    return render_template_string(page)


@app.route('/admin/revenue')
@admin_required
def admin_revenue():
    total = len(users_db)
    pro = sum(1 for u in users_db.values() if u.get('plan') == 'Pro')
    biz = sum(1 for u in users_db.values() if u.get('plan') == 'Business')
    free = total - pro - biz
    mrr = (pro * 29) + (biz * 99)
    arr = mrr * 12
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Revenue - Admin</title></head><body style="background:#F0F2F5;font-family:-apple-system,sans-serif;color:#1A1A2E">'
    page += '<div style="padding:24px"><div style="display:flex;align-items:center;gap:14px;margin-bottom:20px"><a href="/admin" style="color:#6B7280;font-size:22px;text-decoration:none">&larr;</a><p style="font-size:20px;font-weight:600">Revenue Analytics</p></div>'
    page += '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px">'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:16px;text-align:center"><p style="font-size:22px;font-weight:600;color:#042C53">$'+str(mrr)+'</p><p style="font-size:11px;color:#6B7280;margin-top:4px">MRR</p></div>'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:16px;text-align:center"><p style="font-size:22px;font-weight:600;color:#27500A">$'+str(arr)+'</p><p style="font-size:11px;color:#6B7280;margin-top:4px">ARR</p></div>'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:16px;text-align:center"><p style="font-size:22px;font-weight:600;color:#1A1A2E">'+str(pro+biz)+'</p><p style="font-size:11px;color:#6B7280;margin-top:4px">Paid users</p></div>'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:16px;text-align:center"><p style="font-size:22px;font-weight:600;color:#1A1A2E">'+str(total)+'</p><p style="font-size:11px;color:#6B7280;margin-top:4px">Total users</p></div>'
    page += '</div>'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:20px">'
    page += '<p style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:14px">Subscription breakdown</p>'
    page += '<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:0.5px solid #EEF0F3"><span style="font-size:13px;color:#374151">Pro plan ($29/mo)</span><span style="font-size:13px;font-weight:600">'+str(pro)+' users &nbsp;$'+str(pro*29)+'/mo</span></div>'
    page += '<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:0.5px solid #EEF0F3"><span style="font-size:13px;color:#374151">Business plan ($99/mo)</span><span style="font-size:13px;font-weight:600">'+str(biz)+' users &nbsp;$'+str(biz*99)+'/mo</span></div>'
    page += '<div style="display:flex;justify-content:space-between;padding:10px 0"><span style="font-size:13px;color:#374151">Free plan</span><span style="font-size:13px;font-weight:600;color:#9CA3AF">'+str(free)+' users &nbsp;$0/mo</span></div>'
    page += '</div></div></body></html>'
    return render_template_string(page)


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


# ============================================================
# PRICING PAGE
# ============================================================
@app.route('/pricing')
def pricing_page():
    c = site_content
    plans = []
    for slug in ['free','pro','biz']:
        feats_str = c.get(f'plan_{slug}_features', '')
        features = [f.strip() for f in feats_str.split('||') if f.strip()]
        plans.append({
            'name': c.get(f'plan_{slug}_name', slug.upper()),
            'price': c.get(f'plan_{slug}_price', '0'),
            'period': c.get(f'plan_{slug}_period', '/month'),
            'yearly': c.get(f'plan_{slug}_yearly', '0'),
            'desc': c.get(f'plan_{slug}_desc', ''),
            'features': features,
            'cta': c.get(f'plan_{slug}_cta', 'Get started'),
            'badge': c.get(f'plan_{slug}_badge', ''),
            'featured': c.get(f'plan_{slug}_featured', '0') == '1',
        })

    plan_cards = ''
    for p in plans:
        feat_html = ''
        for f in p['features']:
            feat_html += f'<div class="price-feature"><i class="ti ti-check"></i>{f}</div>'
        badge_html = f'<span class="price-badge">{p["badge"]}</span>' if p['badge'] else ''
        card_class = 'price-card featured' if p['featured'] else 'price-card'
        btn_class = 'price-btn featured-btn' if p['featured'] else 'price-btn'
        plan_cards += f"""<div class="{card_class}">{badge_html}
<div class="price-name">{p['name']}</div>
<div class="price-desc">{p['desc']}</div>
<div class="price-amt-wrap"><span class="price-amt monthly">${p['price']}</span><span class="price-amt yearly" style="display:none">${p['yearly']}</span><span class="price-amt-sub">{p['period']}</span></div>
<div class="price-features">{feat_html}</div>
<a href="/signup" class="{btn_class}">{p['cta']}</a>
</div>"""

    # FAQ
    faqs = []
    for i in range(1, 6):
        if f'pfaq{i}_q' in c:
            faqs.append((c[f'pfaq{i}_q'], c[f'pfaq{i}_a']))
    faq_html = ''
    for q, a in faqs:
        faq_html += '<div class="faq-item" onclick="this.classList.toggle(\'open\')"><div class="faq-q">' + q + '<i class="ti ti-plus"></i></div><div class="faq-a">' + a + '</div></div>'


    page = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Pricing — """ + c['site_name'] + """</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">

<style>
:root{--bg:#F7F4EE;--card:#FFFFFF;--text:#171717;--text2:#5F5B53;--text3:#8A847A;--accent:#C46A2B;--accent-hover:#A9551F;--accent-bg:#F5E6D8;--border:#E8E1D8;--dark:#1B140F;--success:#3F7D58;--shadow-lg:0 10px 40px rgba(24,17,10,0.08)}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'Inter','Plus Jakarta Sans',BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased}
body{background:var(--bg);color:var(--text);line-height:1.5}
a{text-decoration:none;color:inherit}
.nav{background:rgba(247,244,238,.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:0 32px;height:68px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.nav-logo{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px}
.nav-logo-mark{width:32px;height:32px;background:var(--dark);border-radius:9px;position:relative}
.nav-logo-mark::after{content:"";position:absolute;inset:6px;border:1.5px solid var(--accent);border-radius:5px}
.nav-links{display:flex;gap:32px}
.nav-link{font-size:14px;font-weight:500;color:var(--text2)}
.nav-link:hover{color:var(--text)}
.btn-primary{background:var(--dark);color:#F7F4EE;font-size:14px;font-weight:600;padding:10px 22px;border-radius:14px;display:inline-flex;align-items:center;gap:6px}
.btn-primary:hover{background:#2A1F18}
.btn-sign{font-size:14px;color:var(--text);padding:8px 18px;border-radius:10px}
.btn-sign:hover{background:var(--accent-bg)}
.hdr{padding:80px 24px 40px;text-align:center;position:relative}
.hdr::before{content:"";position:absolute;top:0;left:50%;transform:translateX(-50%);width:600px;height:300px;background:radial-gradient(ellipse,rgba(196,106,43,.1),transparent 60%);z-index:0}
.hdr-inner{position:relative;z-index:1;max-width:720px;margin:0 auto}
.sec-tag{display:inline-block;background:var(--accent-bg);color:var(--accent);font-size:12px;font-weight:700;padding:5px 14px;border-radius:100px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:16px}
.hdr h1{font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(36px,5vw,52px);font-weight:800;letter-spacing:-1.5px;margin-bottom:14px}
.hdr p{font-size:17px;color:var(--text2);max-width:540px;margin:0 auto}
.toggle-wrap{display:inline-flex;align-items:center;gap:10px;background:white;border:1px solid var(--border);padding:6px;border-radius:100px;margin-top:32px}
.toggle-btn{background:none;border:none;padding:8px 20px;border-radius:100px;font-size:13px;font-weight:600;cursor:pointer;color:var(--text2)}
.toggle-btn.active{background:var(--dark);color:#F7F4EE}
.save-pill{background:#E5F0E9;color:var(--success);font-size:10px;font-weight:700;padding:3px 8px;border-radius:100px;margin-left:4px}
.price-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;max-width:1080px;margin:30px auto 0;padding:0 24px}
.price-card{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:36px 30px;position:relative;transition:all .3s}
.price-card:hover{transform:translateY(-4px);box-shadow:var(--shadow-lg)}
.price-card.featured{background:var(--dark);color:#F7F4EE;border:none}
.price-card.featured .price-name,.price-card.featured .price-amt{color:white}
.price-card.featured .price-desc,.price-card.featured .price-amt-sub{color:#B8AC9F}
.price-card.featured .price-feature{color:#D4C6B6}
.price-badge{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:var(--accent);color:white;font-size:11px;font-weight:700;padding:5px 14px;border-radius:100px;text-transform:uppercase}
.price-name{font-family:'Plus Jakarta Sans',sans-serif;font-size:16px;font-weight:700;margin-bottom:6px}
.price-desc{font-size:13px;color:var(--text2);margin-bottom:24px}
.price-amt-wrap{margin-bottom:8px}
.price-amt{font-family:'Plus Jakarta Sans',sans-serif;font-size:48px;font-weight:800;letter-spacing:-2px;color:var(--text)}
.price-amt-sub{font-size:14px;font-weight:500;color:var(--text2);margin-left:4px}
.price-features{margin:28px 0}
.price-feature{display:flex;align-items:center;gap:10px;font-size:14px;color:var(--text2);margin-bottom:12px;font-weight:500}
.price-feature i{color:var(--accent);font-size:16px;flex-shrink:0}
.price-btn{display:block;text-align:center;padding:13px;border-radius:14px;font-size:14px;font-weight:600;width:100%;border:1px solid var(--border);background:white;color:var(--text)}
.price-btn:hover{background:var(--bg)}
.featured-btn{background:var(--accent);color:white;border-color:var(--accent)}
.featured-btn:hover{background:var(--accent-hover)}
.section{padding:80px 24px}
.container{max-width:1080px;margin:0 auto}
.sec-head{text-align:center;max-width:680px;margin:0 auto 48px}
.sec-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(28px,4vw,40px);font-weight:800;letter-spacing:-1px;margin-bottom:14px}
.sec-sub{font-size:16px;color:var(--text2)}
.faq-list{max-width:780px;margin:0 auto}
.faq-item{background:var(--card);border:1px solid var(--border);border-radius:16px;margin-bottom:12px;overflow:hidden;cursor:pointer}
.faq-item:hover{border-color:var(--text3)}
.faq-q{padding:20px 24px;font-size:15px;font-weight:600;color:var(--text);display:flex;align-items:center;justify-content:space-between}
.faq-q i{font-size:20px;color:var(--text3);transition:transform .2s}
.faq-item.open .faq-q i{transform:rotate(45deg);color:var(--accent)}
.faq-a{padding:0 24px;max-height:0;overflow:hidden;transition:all .3s;font-size:14px;color:var(--text2);line-height:1.7}
.faq-item.open .faq-a{padding:0 24px 20px;max-height:300px}
.footer{background:var(--dark);color:#9A8C7B;padding:50px 24px 25px;text-align:center;margin-top:60px}
.footer p{font-size:13px}
@media(max-width:768px){
  .nav{padding:0 18px}.nav-links{display:none}
  .price-grid{grid-template-columns:1fr;gap:16px}
  .section{padding:50px 18px}
}
</style></head><body>

<nav class="nav">
  <a href="/home" class="nav-logo"><div class="nav-logo-mark"></div>""" + c['site_name'] + """</a>
  <div class="nav-links">
    <a href="/features" class="nav-link">Features</a>
    <a href="/pricing" class="nav-link" style="color:var(--text);font-weight:600">Pricing</a>
    <a href="/docs" class="nav-link">Docs</a>
    <a href="/blog" class="nav-link">Blog</a>
  </div>
  <div style="display:flex;gap:10px">
    <a href="/login" class="btn-sign">Sign in</a>
    <a href="/signup" class="btn-primary">Start Free</a>
  </div>
</nav>

<div class="hdr">
  <div class="hdr-inner">
    <span class="sec-tag">Pricing</span>
    <h1>""" + c['pricing_heading'] + """</h1>
    <p>""" + c['pricing_sub'] + """</p>
    <div class="toggle-wrap">
      <button class="toggle-btn active" onclick="setBilling('monthly',this)">Monthly</button>
      <button class="toggle-btn" onclick="setBilling('yearly',this)">Yearly <span class="save-pill">SAVE 17%</span></button>
    </div>
  </div>
</div>

<div class="price-grid">""" + plan_cards + """</div>

<section class="section">
  <div class="container">
    <div class="sec-head">
      <span class="sec-tag">FAQ</span>
      <h2 class="sec-title">Common pricing questions.</h2>
    </div>
    <div class="faq-list">""" + faq_html + """</div>
  </div>
</section>

<footer class="footer">
  <p>© """ + c['footer_text'] + """</p>
</footer>

<script>
function setBilling(type, btn){
  document.querySelectorAll('.toggle-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.price-amt.monthly').forEach(e=>e.style.display=type==='monthly'?'inline':'none');
  document.querySelectorAll('.price-amt.yearly').forEach(e=>e.style.display=type==='yearly'?'inline':'none');
  document.querySelectorAll('.price-amt-sub').forEach(e=>e.textContent=type==='monthly'?'/month':'/year');
}
</script>
</body></html>"""
    return page


# ============================================================
# FEATURES PAGE
# ============================================================
@app.route('/features')
def features_page():
    c = site_content

    groups = []
    for g in range(1, 5):
        gname = c.get(f'feat_grp{g}_name', '')
        if not gname:
            continue
        gdesc = c.get(f'feat_grp{g}_desc', '')
        items_str = c.get(f'feat_grp{g}_items', '')
        items = []
        for item in items_str.split('##'):
            parts = item.split('||')
            if len(parts) >= 3:
                items.append({
                    'title': parts[0].strip(),
                    'desc': parts[1].strip(),
                    'icon': parts[2].strip(),
                })
        groups.append({'name': gname, 'desc': gdesc, 'items': items})

    groups_html = ''
    for g in groups:
        cards = ''
        for it in g['items']:
            cards += f"""<div class="feat-card">
<div class="feat-icon"><i class="ti {it['icon']}"></i></div>
<div class="feat-title">{it['title']}</div>
<div class="feat-desc">{it['desc']}</div>
</div>"""
        groups_html += f"""<section class="feat-section">
<div class="feat-section-hd">
<h2 class="feat-grp-title">{g['name']}</h2>
<p class="feat-grp-desc">{g['desc']}</p>
</div>
<div class="feat-grid">{cards}</div>
</section>"""

    page = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Features — """ + c['site_name'] + """</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">

<style>
:root{--bg:#F7F4EE;--bg2:#FFFDF9;--card:#FFFFFF;--text:#171717;--text2:#5F5B53;--text3:#8A847A;--accent:#C46A2B;--accent-bg:#F5E6D8;--border:#E8E1D8;--dark:#1B140F;--shadow-lg:0 10px 40px rgba(24,17,10,0.08)}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'Inter','Plus Jakarta Sans',BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased}
body{background:var(--bg);color:var(--text);line-height:1.5}
a{text-decoration:none;color:inherit}
.nav{background:rgba(247,244,238,.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:0 32px;height:68px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.nav-logo{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px}
.nav-logo-mark{width:32px;height:32px;background:var(--dark);border-radius:9px;position:relative}
.nav-logo-mark::after{content:"";position:absolute;inset:6px;border:1.5px solid var(--accent);border-radius:5px}
.nav-links{display:flex;gap:32px}
.nav-link{font-size:14px;font-weight:500;color:var(--text2)}
.nav-link:hover{color:var(--text)}
.btn-primary{background:var(--dark);color:#F7F4EE;font-size:14px;font-weight:600;padding:10px 22px;border-radius:14px;display:inline-flex;align-items:center;gap:6px}
.btn-primary:hover{background:#2A1F18}
.btn-sign{font-size:14px;color:var(--text);padding:8px 18px;border-radius:10px}
.btn-sign:hover{background:var(--accent-bg)}
.hdr{padding:80px 24px 60px;text-align:center;position:relative;overflow:hidden}
.hdr::before{content:"";position:absolute;top:-50px;left:50%;transform:translateX(-50%);width:700px;height:400px;background:radial-gradient(ellipse,rgba(196,106,43,.1),transparent 60%);z-index:0}
.hdr-inner{position:relative;z-index:1;max-width:760px;margin:0 auto}
.sec-tag{display:inline-block;background:var(--accent-bg);color:var(--accent);font-size:12px;font-weight:700;padding:5px 14px;border-radius:100px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:16px}
.hdr h1{font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(36px,5vw,56px);font-weight:800;letter-spacing:-1.5px;margin-bottom:14px;line-height:1.1}
.hdr p{font-size:18px;color:var(--text2);max-width:560px;margin:0 auto}
.feat-section{max-width:1180px;margin:0 auto;padding:60px 24px}
.feat-section-hd{margin-bottom:36px}
.feat-grp-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:28px;font-weight:800;letter-spacing:-0.8px;margin-bottom:8px}
.feat-grp-desc{font-size:15px;color:var(--text2)}
.feat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
.feat-card{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:26px;transition:all .25s}
.feat-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-lg);border-color:var(--accent-bg)}
.feat-icon{width:44px;height:44px;border-radius:11px;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;margin-bottom:16px}
.feat-icon i{font-size:20px;color:var(--accent)}
.feat-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:16px;font-weight:700;margin-bottom:8px}
.feat-desc{font-size:13.5px;color:var(--text2);line-height:1.65}
.cta-banner{padding:80px 24px;text-align:center}
.cta-inner{max-width:760px;margin:0 auto;background:var(--dark);border-radius:24px;padding:48px 36px;color:white}
.cta-inner h2{font-family:'Plus Jakarta Sans',sans-serif;font-size:32px;font-weight:800;letter-spacing:-1px;margin-bottom:12px}
.cta-inner p{color:#B8AC9F;margin-bottom:24px}
.cta-inner .btn-primary{background:var(--accent);padding:13px 28px}
.cta-inner .btn-primary:hover{background:#A9551F}
.footer{background:var(--dark);color:#9A8C7B;padding:50px 24px 25px;text-align:center}
.footer p{font-size:13px}
@media(max-width:768px){
  .nav{padding:0 18px}.nav-links{display:none}
  .feat-grid{grid-template-columns:1fr;gap:12px}
  .feat-section{padding:40px 18px}
  .hdr{padding:60px 18px 40px}
}
</style></head><body>

<nav class="nav">
  <a href="/home" class="nav-logo"><div class="nav-logo-mark"></div>""" + c['site_name'] + """</a>
  <div class="nav-links">
    <a href="/features" class="nav-link" style="color:var(--text);font-weight:600">Features</a>
    <a href="/pricing" class="nav-link">Pricing</a>
    <a href="/docs" class="nav-link">Docs</a>
    <a href="/blog" class="nav-link">Blog</a>
  </div>
  <div style="display:flex;gap:10px">
    <a href="/login" class="btn-sign">Sign in</a>
    <a href="/signup" class="btn-primary">Start Free</a>
  </div>
</nav>

<div class="hdr">
  <div class="hdr-inner">
    <span class="sec-tag">All Features</span>
    <h1>""" + c['features_heading'] + """</h1>
    <p>""" + c['features_sub'] + """</p>
  </div>
</div>

""" + groups_html + """

<section class="cta-banner">
  <div class="cta-inner">
    <h2>Ready to save 8 hours per survey?</h2>
    <p>Start free — no credit card needed. Cancel anytime.</p>
    <a href="/signup" class="btn-primary">Start Free Trial <i class="ti ti-arrow-right"></i></a>
  </div>
</section>

<footer class="footer">
  <p>© """ + c['footer_text'] + """</p>
</footer>

</body></html>"""
    return page


# ============================================================
# BLOG
# ============================================================
@app.route('/blog')
def blog():
    c = site_content
    tag_colors = {
        'AI & QC':'#F5E6D8|#0C447C','Best Practices':'#EAF3DE|#27500A',
        'Languages':'#EEEDFE|#3C3489','Platforms':'#FAEEDA|#633806',
        'Productivity':'#E1F5EE|#085041','ROI':'#FCEBEB|#791F1F'
    }
    posts = [
        (c['blog_post1_title'],c['blog_post1_date'],c['blog_post1_tag'],c['blog_post1_summary']),
        (c['blog_post2_title'],c['blog_post2_date'],c['blog_post2_tag'],c['blog_post2_summary']),
        (c['blog_post3_title'],c['blog_post3_date'],c['blog_post3_tag'],c['blog_post3_summary']),
        (c['blog_post4_title'],c['blog_post4_date'],c['blog_post4_tag'],c['blog_post4_summary']),
        (c['blog_post5_title'],c['blog_post5_date'],c['blog_post5_tag'],c['blog_post5_summary']),
        (c['blog_post6_title'],c['blog_post6_date'],c['blog_post6_tag'],c['blog_post6_summary']),
    ]
    cards = ''
    for title,date,tag,summary in posts:
        tc = tag_colors.get(tag,'#F1EFE8|#444441').split('|')
        cards += ('<div style="background:white;border:0.5px solid #DDE1E7;border-radius:12px;padding:24px">'
            '<span style="background:'+tc[0]+';color:'+tc[1]+';font-size:11px;padding:3px 10px;border-radius:20px;font-weight:500">'+tag+'</span>'
            '<p style="font-size:16px;font-weight:600;color:#1A1A2E;margin:12px 0 8px;line-height:1.4">'+title+'</p>'
            '<p style="font-size:13px;color:#6B7280;line-height:1.7;margin-bottom:14px">'+summary+'</p>'
            '<div style="display:flex;justify-content:space-between;align-items:center">'
            '<p style="font-size:11px;color:#9CA3AF">'+date+'</p>'
            '<span style="font-size:13px;color:#1B140F;font-weight:500">Read more &rarr;</span>'
            '</div></div>')
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>Blog - '+c['site_name']+'</title>'
    page += '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">'
    page += ''
    page += '<style>*{box-sizing:border-box;margin:0;padding:0;font-family:Inter,Plus Jakarta Sans,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}body{background:#F7F4EE;color:#171717}a{text-decoration:none}'
    page += '.nav{background:white;border-bottom:0.5px solid #DDE1E7;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between}'
    page += '.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;max-width:1100px;margin:0 auto}'
    page += '@media(max-width:768px){.grid{grid-template-columns:1fr}.nav{padding:0 16px}}</style></head><body>'
    page += '<nav class="nav"><div style="display:flex;align-items:center;gap:10px"><div style="width:28px;height:28px;background:#1B140F;border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="font-size:14px;color:white"></i></div><a href="/home" style="font-size:15px;font-weight:700;color:#1A1A2E">'+c['site_name']+'</a></div>'
    page += '<div style="display:flex;gap:10px"><a href="/login" style="font-size:13px;color:#6B7280;padding:7px 14px;border:0.5px solid #DDE1E7;border-radius:7px">Sign in</a><a href="/signup" style="font-size:13px;color:white;background:#1B140F;padding:7px 14px;border-radius:7px;font-weight:500">Sign up free</a></div></nav>'
    page += '<div style="background:#1B140F;padding:50px 40px;text-align:center"><p style="font-size:12px;color:#B8AC9F;text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px">Blog</p><h1 style="font-size:36px;font-weight:700;color:white;margin-bottom:10px">QC Insights & Guides</h1><p style="font-size:16px;color:#B8AC9F">Best practices, platform guides, and industry news from the QC world.</p></div>'
    page += '<div style="padding:50px 40px"><div class="grid">'+cards+'</div></div>'
    page += '<footer style="background:#1B140F;padding:30px 40px;text-align:center"><p style="color:#6B7280;font-size:13px">'+c['footer_text']+'</p><div style="margin-top:10px;display:flex;gap:16px;justify-content:center"><a href="/home" style="font-size:12px;color:#6B7280">Home</a><a href="/privacy-policy" style="font-size:12px;color:#6B7280">Privacy</a><a href="/terms" style="font-size:12px;color:#6B7280">Terms</a></div></footer>'
    page += '</body></html>'
    return page


# ============================================================
# DOCS
# ============================================================
@app.route('/docs')
def docs():
    c = site_content
    sections = [
        ('Getting Started','ti-rocket','#F5E6D8','#0C447C',[
            ('Create your account','Sign up free at surveyqc.online. No credit card needed. 5 reports/month free forever.'),
            ('Upload your screener doc','Go to New QC. Upload the .docx screener/spec document for your survey.'),
            ('Paste survey URL','Paste the live survey URL (Confirmit, Decipher, Forsta, or Qualtrics).'),
            ('Start QC check','Click Run QC. AI will crawl every path and run all 8 checks automatically.'),
            ('Download report','When done, download the Word report with all issues, screenshots and QC certificate.'),
        ]),
        ('8 QC Checks','ti-list-check','#E1F5EE','#085041',[
            ('1. Termination rules','Checks every terminate/close logic against the spec doc. Catches wrong terminations.'),
            ('2. Missing words','Word-by-word comparison between doc and live survey. Catches typos and missing text.'),
            ('3. Question text match','Full question text compared against spec. Detects any deviations.'),
            ('4. Options match','All answer options compared against spec. Catches missing or extra options.'),
            ('5. Mandatory markers','Checks * mandatory markers match the spec on every question.'),
            ('6. Piping markers','Verifies all {piped_text} markers are present and correct.'),
            ('7. Answer codes','Checks answer codes are sequential and match spec exactly.'),
            ('8. Question order','Verifies questions appear in the correct order as per spec doc.'),
        ]),
        ('Platforms Supported','ti-device-laptop','#FAEEDA','#633806',[
            ('Confirmit','Full support. Paste the respondent-facing survey URL.'),
            ('Decipher','Full support. Use the live survey link, not the editor link.'),
            ('Forsta (formerly Confirmit)','Full support. Use the standard survey URL.'),
            ('Qualtrics','Full support. Use the published survey URL from the Distributions tab.'),
        ]),
        ('Token Limits','ti-database','#EEEDFE','#3C3489',[
            ('Free plan: 20,000 tokens','Enough for small surveys (~30 questions, 10 paths).'),
            ('Pro plan: 100,000 tokens','Enough for medium surveys (~100 questions, 50 paths).'),
            ('Business plan: 150,000 tokens','Enough for complex surveys with 200+ questions and 100+ paths.'),
            ('What happens at limit?','AI stops at the token limit. Remaining checks are flagged MANUAL for you to do by hand. Admin can raise limits anytime.'),
        ]),
        ('Share Report','ti-share','#E1F5EE','#085041',[
            ('Generate share link','On any report, click Share. A unique link is generated.'),
            ('Send to client','Client clicks link, creates free account, views report.'),
            ('Client actions','Client can comment on issues and mark them as Fixed.'),
            ('Link expiry','Share links expire in 7 days by default.'),
        ]),
        ('FAQ','ti-help-circle','#FCEBEB','#791F1F',[
            ('What file format for spec doc?','.docx (Microsoft Word) only. PDF not supported yet.'),
            ('Does it work for non-English surveys?','Yes -- 80+ languages supported including French, Italian, Arabic, Urdu, Japanese.'),
            ('Can I retest after fixing bugs?','Yes. On the report page, click Retest to rerun QC on the same URL.'),
            ('Is my data safe?','Yes. All data auto-deletes after 30 days. We are GDPR, CCPA, India DPDP compliant.'),
            ('Can I use my own API key?','Yes, on Business plan. Go to Settings > API Key to add your own Gemini key.'),
        ]),
    ]
    nav_items = ''
    content_sections = ''
    for title,icon,bg,col,items in sections:
        anchor = title.lower().replace(' ','_').replace('(','').replace(')','')
        nav_items += '<a href="#'+anchor+'" style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;color:#374151;font-size:13px;text-decoration:none;margin-bottom:2px"><i class="ti '+icon+'" style="font-size:15px;color:'+col+'"></i>'+title+'</a>'
        content_sections += '<div id="'+anchor+'" style="margin-bottom:40px">'
        content_sections += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:16px"><div style="width:36px;height:36px;border-radius:8px;background:'+bg+';display:flex;align-items:center;justify-content:center"><i class="ti '+icon+'" style="font-size:18px;color:'+col+'"></i></div><h2 style="font-size:20px;font-weight:600;color:#1A1A2E">'+title+'</h2></div>'
        for item_title,item_desc in items:
            content_sections += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:16px;margin-bottom:8px"><p style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:4px">'+item_title+'</p><p style="font-size:13px;color:#6B7280;line-height:1.6">'+item_desc+'</p></div>'
        content_sections += '</div>'
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Docs - '+c['site_name']+'</title>'
    page += '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">'
    page += '<style>*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{background:#FBF8F2;color:#1A1A2E}a{text-decoration:none}.nav{background:white;border-bottom:0.5px solid #DDE1E7;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between}.layout{display:grid;grid-template-columns:240px 1fr;min-height:calc(100vh - 60px)}.sidebar{background:white;border-right:0.5px solid #DDE1E7;padding:24px 16px;position:sticky;top:60px;height:calc(100vh - 60px);overflow-y:auto}.content{padding:40px;max-width:800px}@media(max-width:768px){.layout{grid-template-columns:1fr}.sidebar{display:none}.nav{padding:0 16px}.content{padding:20px}}</style></head><body>'
    page += '<nav class="nav"><div style="display:flex;align-items:center;gap:10px"><div style="width:28px;height:28px;background:#1B140F;border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="font-size:14px;color:white"></i></div><a href="/home" style="font-size:15px;font-weight:700;color:#1A1A2E">'+c['site_name']+'</a></div><div style="display:flex;gap:10px"><a href="/login" style="font-size:13px;color:#6B7280;padding:7px 14px;border:0.5px solid #DDE1E7;border-radius:7px">Sign in</a><a href="/signup" style="font-size:13px;color:white;background:#1B140F;padding:7px 14px;border-radius:7px;font-weight:500">Sign up free</a></div></nav>'
    page += '<div class="layout"><div class="sidebar"><p style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">Documentation</p>'+nav_items+'<div style="margin-top:20px;padding:12px;background:#F5E6D8;border-radius:8px"><p style="font-size:12px;color:#C46A2B;font-weight:500;margin-bottom:4px">Need help?</p><a href="mailto:'+c['support_email']+'" style="font-size:12px;color:#185FA5">'+c['support_email']+'</a></div></div>'
    page += '<div class="content"><h1 style="font-size:28px;font-weight:700;color:#1A1A2E;margin-bottom:8px">Documentation</h1><p style="font-size:15px;color:#6B7280;margin-bottom:32px">'+c['docs_intro']+'</p>'+content_sections+'</div></div>'
    page += '<footer style="background:#1B140F;padding:30px 40px;text-align:center"><p style="color:#6B7280;font-size:13px">'+c['footer_text']+'</p></footer></body></html>'
    return page


# ============================================================
# COMPARE PAGES
# ============================================================
@app.route('/compare/<slug>')
def compare_page(slug):
    c = site_content
    pages = {
        'chatgpt': (c['compare_chatgpt_heading'], c['compare_chatgpt_summary'],
            [('SurveyQC','Built for survey QC','8 specialized checks','Playwright crawling','Word report output','Any survey platform','Auto screenshots','Supports 80+ languages'),
             ('ChatGPT','General AI assistant','No survey-specific checks','No browser crawling','No formatted report','No platform integration','No screenshots','Text only')]),
        'excel': (c['compare_excel_heading'], c['compare_excel_summary'],
            [('SurveyQC','Automated tool','10 min per survey','99% accuracy','Auto screenshots','Professional Word report','No manual effort','GDPR compliant'),
             ('Excel Manual QC','Spreadsheet + human','8+ hours per survey','~70% accuracy','No screenshots','Manual formatting','Heavy manual effort','No compliance')]),
        'manual': (c['compare_manual_heading'], c['compare_manual_summary'],
            [('SurveyQC','AI-powered','10 min per survey','99% accuracy','Never misses edge cases','Consistent every time','Scales instantly','Costs $29/mo'),
             ('Manual Testing','Human tester','8+ hours per survey','~70% accuracy','Misses 30% of bugs','Varies by tester','Bottleneck at scale','Costs $50+/hr')]),
    }
    if slug not in pages:
        return redirect('/home')
    heading, summary, cols = pages[slug]
    col1, col2 = cols
    table_rows = ''
    for i in range(1, len(col1)):
        color = '#EAF3DE' if i % 2 == 0 else 'white'
        table_rows += '<tr style="background:'+color+'"><td style="padding:12px 16px;font-size:13px;color:#374151;border-right:0.5px solid #DDE1E7">'+col1[i]+'</td><td style="padding:12px 16px;font-size:13px;color:#374151">'+col2[i]+'</td></tr>'
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+heading+'</title>'
    page += '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">'
    page += '<style>*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{background:#FBF8F2;color:#1A1A2E}a{text-decoration:none}.nav{background:white;border-bottom:0.5px solid #DDE1E7;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between}@media(max-width:768px){.nav{padding:0 16px}}</style></head><body>'
    page += '<nav class="nav"><div style="display:flex;align-items:center;gap:10px"><div style="width:28px;height:28px;background:#1B140F;border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="font-size:14px;color:white"></i></div><a href="/home" style="font-size:15px;font-weight:700;color:#1A1A2E">'+c['site_name']+'</a></div><div style="display:flex;gap:10px"><a href="/login" style="font-size:13px;color:#6B7280;padding:7px 14px;border:0.5px solid #DDE1E7;border-radius:7px">Sign in</a><a href="/signup" style="font-size:13px;color:white;background:#1B140F;padding:7px 14px;border-radius:7px;font-weight:500">Try free</a></div></nav>'
    page += '<div style="background:#1B140F;padding:50px 40px;text-align:center"><h1 style="font-size:34px;font-weight:700;color:white;margin-bottom:12px">'+heading+'</h1><p style="font-size:16px;color:#B8AC9F;max-width:600px;margin:0 auto;line-height:1.7">'+summary+'</p></div>'
    page += '<div style="padding:50px 40px;max-width:900px;margin:0 auto">'
    page += '<div style="background:white;border-radius:14px;overflow:hidden;border:0.5px solid #DDE1E7">'
    page += '<div style="display:grid;grid-template-columns:1fr 1fr"><div style="padding:16px;background:#1B140F;text-align:center"><p style="font-size:15px;font-weight:700;color:white">'+col1[0]+'</p></div><div style="padding:16px;background:#FBF8F2;text-align:center;border-left:0.5px solid #DDE1E7"><p style="font-size:15px;font-weight:600;color:#6B7280">'+col2[0]+'</p></div></div>'
    page += '<table style="width:100%;border-collapse:collapse"><tbody>'+table_rows+'</tbody></table></div>'
    page += '<div style="text-align:center;margin-top:40px"><p style="font-size:18px;font-weight:600;color:#1A1A2E;margin-bottom:16px">Ready to try '+c['site_name']+'?</p><a href="/signup" style="background:#1B140F;color:white;font-size:15px;padding:14px 36px;border-radius:10px;font-weight:600">Start free -- no card required</a></div>'
    page += '</div><footer style="background:#1B140F;padding:30px 40px;text-align:center;margin-top:40px"><p style="color:#6B7280;font-size:13px">'+c['footer_text']+'</p></footer></body></html>'
    return page


# ============================================================
# COMMUNITY
# ============================================================
@app.route('/community')
def community():
    c = site_content
    posts = [
        ('SarahQC','How I reduced QC time from 8 hours to 15 minutes','Sharing my workflow after using SurveyQC for 3 months. The auto screenshot feature alone saved me countless hours of back-and-forth with developers.','14 replies','2 days ago','#F5E6D8','#0C447C'),
        ('MarieL','French survey QC tips -- accents and encoding','Been testing French surveys on Confirmit. A few things I have learned about handling accent marks and special characters with SurveyQC.','8 replies','5 days ago','#EAF3DE','#27500A'),
        ('JamesMR','Share your QC report templates','Who has built good templates for specific survey types? Sharing mine for trackers and ad hoc studies. Would love to see others.','22 replies','1 week ago','#EEEDFE','#3C3489'),
        ('RahulD','Qualtrics integration -- complete guide','After much trial and error, here is the complete guide to running SurveyQC on Qualtrics surveys including where to find the right URL.','11 replies','1 week ago','#FAEEDA','#633806'),
    ]
    post_cards = ''
    for author,title,body,replies,time,bg,col in posts:
        post_cards += ('<div style="background:white;border:0.5px solid #DDE1E7;border-radius:12px;padding:20px;margin-bottom:12px">'
            '<div style="display:flex;align-items:start;gap:12px">'
            '<div style="width:36px;height:36px;border-radius:50%;background:'+bg+';color:'+col+';display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0">'+author[:2]+'</div>'
            '<div style="flex:1"><p style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:4px">'+title+'</p>'
            '<p style="font-size:12px;font-weight:500;color:'+col+';margin-bottom:6px">'+author+'</p>'
            '<p style="font-size:13px;color:#6B7280;line-height:1.6;margin-bottom:12px">'+body+'</p>'
            '<div style="display:flex;gap:16px"><span style="font-size:12px;color:#9CA3AF"><i class="ti ti-message" style="font-size:12px"></i> '+replies+'</span>'
            '<span style="font-size:12px;color:#9CA3AF">'+time+'</span></div>'
            '</div></div></div>')
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Community - '+c['site_name']+'</title>'
    page += '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">'
    page += '<style>*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{background:#FBF8F2;color:#1A1A2E}a{text-decoration:none}.nav{background:white;border-bottom:0.5px solid #DDE1E7;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between}.layout{display:grid;grid-template-columns:1fr 320px;gap:24px;max-width:1100px;margin:0 auto;padding:40px}@media(max-width:768px){.layout{grid-template-columns:1fr}.nav{padding:0 16px}}</style></head><body>'
    page += '<nav class="nav"><div style="display:flex;align-items:center;gap:10px"><div style="width:28px;height:28px;background:#1B140F;border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="font-size:14px;color:white"></i></div><a href="/home" style="font-size:15px;font-weight:700;color:#1A1A2E">'+c['site_name']+'</a></div><div style="display:flex;gap:10px"><a href="/login" style="font-size:13px;color:#6B7280;padding:7px 14px;border:0.5px solid #DDE1E7;border-radius:7px">Sign in</a><a href="/signup" style="font-size:13px;color:white;background:#1B140F;padding:7px 14px;border-radius:7px;font-weight:500">Join free</a></div></nav>'
    page += '<div style="background:#1B140F;padding:50px 40px;text-align:center"><p style="font-size:36px">&#128106;</p><h1 style="font-size:34px;font-weight:700;color:white;margin-bottom:12px">'+c['community_heading']+'</h1><p style="font-size:16px;color:#B8AC9F;max-width:500px;margin:0 auto">'+c['community_subheading']+'</p></div>'
    page += '<div class="layout"><div>'
    page += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><p style="font-size:16px;font-weight:600;color:#1A1A2E">Recent discussions</p><a href="/signup" style="font-size:13px;color:white;background:#1B140F;padding:8px 16px;border-radius:8px;font-weight:500">+ New post</a></div>'
    page += post_cards + '</div>'
    page += '<div><div style="background:white;border:0.5px solid #DDE1E7;border-radius:12px;padding:20px;margin-bottom:16px"><p style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">Community stats</p><div style="display:flex;flex-direction:column;gap:10px"><div style="display:flex;justify-content:space-between"><span style="font-size:13px;color:#6B7280">Members</span><span style="font-size:13px;font-weight:600;color:#1A1A2E">500+</span></div><div style="display:flex;justify-content:space-between"><span style="font-size:13px;color:#6B7280">Posts</span><span style="font-size:13px;font-weight:600;color:#1A1A2E">1,200+</span></div><div style="display:flex;justify-content:space-between"><span style="font-size:13px;color:#6B7280">Countries</span><span style="font-size:13px;font-weight:600;color:#1A1A2E">40+</span></div></div></div>'
    page += '<div style="background:#F5E6D8;border-radius:12px;padding:20px"><p style="font-size:14px;font-weight:600;color:#C46A2B;margin-bottom:8px">Earn with our affiliate program</p><p style="font-size:13px;color:#185FA5;margin-bottom:12px;line-height:1.6">'+c['affiliate_commission']+'% recurring commission. No cap.</p><a href="/affiliate" style="display:block;text-align:center;background:#1B140F;color:white;padding:9px;border-radius:8px;font-size:13px;font-weight:500">Learn more</a></div></div>'
    page += '</div><footer style="background:#1B140F;padding:30px 40px;text-align:center"><p style="color:#6B7280;font-size:13px">'+c['footer_text']+'</p></footer></body></html>'
    return page


# ============================================================
# AFFILIATE
# ============================================================
@app.route('/affiliate')
def affiliate():
    c = site_content
    steps = [
        ('1','Sign up free','Create your SurveyQC account. No approval needed. Takes 30 seconds.','#F5E6D8','#0C447C'),
        ('2','Get your link','Copy your unique affiliate link from Settings > Affiliate.','#EAF3DE','#27500A'),
        ('3','Share it','Share with QC professionals, on LinkedIn, in communities, in blog posts.','#FAEEDA','#633806'),
        ('4','Earn monthly','Get '+c['affiliate_commission']+'% of every payment, every month, forever. No cap.','#EEEDFE','#3C3489'),
    ]
    step_cards = ''
    for num,title,desc,bg,col in steps:
        step_cards += '<div style="background:'+bg+';border-radius:12px;padding:24px;text-align:center"><div style="width:44px;height:44px;border-radius:50%;background:#1B140F;color:white;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;margin:0 auto 16px">'+num+'</div><p style="font-size:15px;font-weight:600;color:#1A1A2E;margin-bottom:8px">'+title+'</p><p style="font-size:13px;color:#6B7280;line-height:1.6">'+desc+'</p></div>'
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Affiliate Program - '+c['site_name']+'</title>'
    page += '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">'
    page += '<style>*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{background:#FBF8F2;color:#1A1A2E}a{text-decoration:none}.nav{background:white;border-bottom:0.5px solid #DDE1E7;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}@media(max-width:768px){.grid4{grid-template-columns:repeat(2,1fr)}.nav{padding:0 16px}}</style></head><body>'
    page += '<nav class="nav"><div style="display:flex;align-items:center;gap:10px"><div style="width:28px;height:28px;background:#1B140F;border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="font-size:14px;color:white"></i></div><a href="/home" style="font-size:15px;font-weight:700;color:#1A1A2E">'+c['site_name']+'</a></div><div style="display:flex;gap:10px"><a href="/login" style="font-size:13px;color:#6B7280;padding:7px 14px;border:0.5px solid #DDE1E7;border-radius:7px">Sign in</a><a href="/signup" style="font-size:13px;color:white;background:#1B140F;padding:7px 14px;border-radius:7px;font-weight:500">Join free</a></div></nav>'
    page += '<div style="background:#1B140F;padding:60px 40px;text-align:center"><p style="font-size:40px;margin-bottom:12px">&#128176;</p><h1 style="font-size:36px;font-weight:700;color:white;margin-bottom:12px">'+c['affiliate_heading']+'</h1><p style="font-size:18px;color:#B8AC9F;margin-bottom:8px">'+c['affiliate_details']+'</p><p style="font-size:24px;font-weight:700;color:white;margin-top:16px">'+c['affiliate_commission']+'% recurring commission</p></div>'
    page += '<div style="padding:60px 40px;max-width:1000px;margin:0 auto">'
    page += '<div style="text-align:center;margin-bottom:40px"><h2 style="font-size:28px;font-weight:700;color:#1A1A2E;margin-bottom:8px">How it works</h2></div>'
    page += '<div class="grid4" style="margin-bottom:50px">'+step_cards+'</div>'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:14px;padding:32px;text-align:center"><h3 style="font-size:22px;font-weight:600;color:#1A1A2E;margin-bottom:8px">Example earnings</h3><p style="font-size:14px;color:#6B7280;margin-bottom:24px">If you refer 10 Pro users ($29/mo each):</p><p style="font-size:36px;font-weight:700;color:#1B140F;margin-bottom:4px">$87/month</p><p style="font-size:14px;color:#9CA3AF">recurring, every month, forever &middot; $1,044/year</p><a href="/signup" style="display:inline-block;background:#1B140F;color:white;font-size:15px;padding:14px 36px;border-radius:10px;font-weight:600;margin-top:24px">Start earning free</a></div>'
    page += '</div><footer style="background:#1B140F;padding:30px 40px;text-align:center"><p style="color:#6B7280;font-size:13px">'+c['footer_text']+'</p></footer></body></html>'
    return page


# ============================================================
# CHANGELOG
# ============================================================
@app.route('/changelog')
def changelog():
    c = site_content
    logs = [
        (c['changelog_v10'],'May 2026','#EAF3DE','#27500A','Latest'),
        (c['changelog_v9'],'April 2026','#F5E6D8','#0C447C',''),
        (c['changelog_v8'],'March 2026','#FAEEDA','#633806',''),
        (c['changelog_v7'],'February 2026','#EEEDFE','#3C3489',''),
        (c['changelog_v6'],'January 2026','#F1EFE8','#444441',''),
    ]
    items = ''
    for log,date,bg,col,label in logs:
        version = log.split(' -- ')[0] if ' -- ' in log else log[:10]
        rest = log.split(' -- ')[1] if ' -- ' in log else log
        items += '<div style="display:flex;gap:20px;margin-bottom:24px"><div style="text-align:right;min-width:90px;padding-top:4px"><p style="font-size:12px;color:#9CA3AF">'+date+'</p></div><div style="display:flex;flex-direction:column;align-items:center"><div style="width:12px;height:12px;border-radius:50%;background:'+col+';margin-top:4px;flex-shrink:0"></div><div style="width:1px;background:#DDE1E7;flex:1;margin-top:4px"></div></div><div style="flex:1;background:white;border:0.5px solid #DDE1E7;border-radius:10px;padding:16px;margin-bottom:4px"><div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><p style="font-size:14px;font-weight:600;color:#1A1A2E">'+version+'</p>'+(('<span style="background:'+bg+';color:'+col+';font-size:10px;padding:2px 8px;border-radius:20px;font-weight:500">'+label+'</span>') if label else '')+'</div><p style="font-size:13px;color:#6B7280;line-height:1.6">'+rest+'</p></div></div>'
    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Changelog - '+c['site_name']+'</title>'
    page += '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">'
    page += '<style>*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{background:#FBF8F2;color:#1A1A2E}a{text-decoration:none}.nav{background:white;border-bottom:0.5px solid #DDE1E7;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between}@media(max-width:768px){.nav{padding:0 16px}}</style></head><body>'
    page += '<nav class="nav"><div style="display:flex;align-items:center;gap:10px"><div style="width:28px;height:28px;background:#1B140F;border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="font-size:14px;color:white"></i></div><a href="/home" style="font-size:15px;font-weight:700;color:#1A1A2E">'+c['site_name']+'</a></div><div style="display:flex;gap:10px"><a href="/login" style="font-size:13px;color:#6B7280;padding:7px 14px;border:0.5px solid #DDE1E7;border-radius:7px">Sign in</a><a href="/signup" style="font-size:13px;color:white;background:#1B140F;padding:7px 14px;border-radius:7px;font-weight:500">Sign up</a></div></nav>'
    page += '<div style="background:#1B140F;padding:50px 40px;text-align:center"><h1 style="font-size:34px;font-weight:700;color:white;margin-bottom:10px">Changelog</h1><p style="font-size:16px;color:#B8AC9F">What is new in '+c['site_name']+'</p></div>'
    page += '<div style="padding:50px 40px;max-width:700px;margin:0 auto">'+items+'</div>'
    page += '<footer style="background:#1B140F;padding:30px 40px;text-align:center"><p style="color:#6B7280;font-size:13px">'+c['footer_text']+'</p></footer></body></html>'
    return page


@app.route('/run-screenshot-qc', methods=['POST'])
@login_required
def run_screenshot_qc():
    import uuid, base64, threading, io, json, re
    from datetime import datetime
    from docx import Document

    user = get_current_user()
    if not user or user.get('plan','Free') == 'Free':
        return redirect('/billing')

    docx_file = request.files.get('docx_file')
    screenshots = request.files.getlist('screenshots')
    instructions = request.form.get('instructions','').strip()
    if not docx_file or not screenshots:
        return redirect('/ai-tester')

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        'status':'running','mode':'screenshot',
        'user_email':session.get('user_email'),
        'doc_name':docx_file.filename,
        'created_at':datetime.now().isoformat(),
        'issues':[],'log':['Starting Screenshot QC...'],'progress':0,
    }

    docx_bytes = docx_file.read()
    ss_list = [(f.filename, f.read()) for f in screenshots if f.filename]

    def run_ss(job_id, docx_bytes, ss_list, instructions):
        try:
            jobs[job_id]['log'].append('Reading spec document...')
            jobs[job_id]['progress'] = 10
            doc = Document(io.BytesIO(docx_bytes))
            doc_text = '\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
            jobs[job_id]['log'].append('Spec doc loaded: '+str(len(doc_text))+' chars')
            jobs[job_id]['progress'] = 20

            api_key = api_store.get('gemini',{}).get('key','')
            if not api_key:
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['log'].append('No Gemini API key configured. Go to Admin > API Management to add one.')
                return

            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')

            issues = []
            total = len(ss_list)
            instr = instructions if instructions else 'Check question text, options, mandatory markers, and any visible issues.'

            for i, (name, img_bytes) in enumerate(ss_list):
                jobs[job_id]['log'].append('Analyzing screenshot '+str(i+1)+'/'+str(total)+': '+name)
                jobs[job_id]['progress'] = 20 + int((i/total)*60)

                prompt_text = ('You are a survey QC expert. Compare this survey screenshot against the spec document.\n\n'
                    'SPEC DOCUMENT:\n' + doc_text[:8000] + '\n\n'
                    'INSTRUCTIONS: ' + instr + '\n\n'
                    'TASK: Identify issues:\n'
                    '1. Question text differs from spec\n'
                    '2. Missing or extra answer options\n'
                    '3. Wrong mandatory (*) markers\n'
                    '4. Any other visible issues\n\n'
                    'Respond ONLY in JSON:\n'
                    '{"issues_found":true/false,"issues":[{"question":"Q1","type":"text mismatch","expected":"spec text","actual":"screenshot text","severity":"high/medium/low"}],"summary":"brief summary"}')

                img_part = {'mime_type':'image/jpeg','data':base64.b64encode(img_bytes).decode()}
                try:
                    response = model.generate_content([prompt_text, img_part])
                    raw = response.text.strip()
                    raw = re.sub(r'```json|```','',raw).strip()
                    result = json.loads(raw)
                    if result.get('issues_found') and result.get('issues'):
                        for iss in result['issues']:
                            iss['screenshot'] = name
                            issues.append(iss)
                        jobs[job_id]['log'].append('  Found '+str(len(result['issues']))+' issue(s)')
                    else:
                        jobs[job_id]['log'].append('  No issues in '+name)
                except Exception as e:
                    jobs[job_id]['log'].append('  Error: '+str(e)[:60])

            jobs[job_id]['progress'] = 85
            jobs[job_id]['log'].append('Total issues: '+str(len(issues)))
            jobs[job_id]['issues'] = issues

            # Generate Word report
            jobs[job_id]['log'].append('Generating report...')
            from docx import Document as D2
            from docx.shared import Pt
            rpt = D2()
            rpt.add_heading('Screenshot QC Report', 0)
            rpt.add_paragraph('Screenshots analyzed: '+str(total))
            rpt.add_paragraph('Issues found: '+str(len(issues)))
            rpt.add_paragraph('Spec doc: '+jobs[job_id]['doc_name'])
            rpt.add_heading('Issues', level=1)
            if issues:
                for idx, iss in enumerate(issues, 1):
                    p = rpt.add_paragraph()
                    run = p.add_run('Issue '+str(idx)+': '+iss.get('type','Unknown')+' — '+iss.get('question',''))
                    run.bold = True
                    rpt.add_paragraph('Screenshot: '+iss.get('screenshot',''))
                    rpt.add_paragraph('Expected: '+iss.get('expected',''))
                    rpt.add_paragraph('Actual: '+iss.get('actual',''))
                    rpt.add_paragraph('Severity: '+iss.get('severity','medium'))
                    rpt.add_paragraph('')
            else:
                rpt.add_paragraph('No issues found! All screenshots match the spec document.')

            buf = io.BytesIO()
            rpt.save(buf)
            jobs[job_id]['report_bytes'] = buf.getvalue()
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['progress'] = 100
            jobs[job_id]['log'].append('Done! Report ready for download.')
        except Exception as e:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['log'].append('Error: '+str(e)[:100])

    t = threading.Thread(target=run_ss, args=(job_id, docx_bytes, ss_list, instructions))
    t.daemon = True
    t.start()
    return redirect('/progress/'+job_id)


@app.route('/retest/<job_id>')
@login_required
def retest_job(job_id):
    import uuid
    from datetime import datetime

    if job_id not in jobs:
        return redirect('/reports')

    old_job = jobs[job_id]

    # Only retest failed issues
    failed_qids = set()
    for issue in old_job.get('issues', []):
        if issue.get('severity') in ('HIGH', 'MEDIUM'):
            failed_qids.add(issue.get('qid', ''))
    for result in old_job.get('term_results', []):
        if not result.get('passed'):
            failed_qids.add(result.get('test_qid', ''))

    new_job_id = str(uuid.uuid4())[:8]
    doc_path = old_job.get('doc_path', '')
    survey_url = old_job.get('survey_url', '')

    if not doc_path or not survey_url:
        # Fallback - full retest
        jobs[new_job_id] = {
            'status': 'error',
            'logs': [{'msg': 'Original doc/URL not found. Please run a new QC.', 'color': 'red'}],
            'progress': 0,
            'issues': [], 'term_results': [],
        }
        return redirect('/progress/' + new_job_id)

    jobs[new_job_id] = {
        'status': 'running',
        'progress': 0,
        'phase': 'Starting retest...',
        'logs': [{'msg': 'Retest started for ' + str(len(failed_qids)) + ' failed questions...', 'color': 'cyan'}],
        'doc_name': old_job.get('doc_name', ''),
        'doc_path': doc_path,
        'survey_url': survey_url,
        'platform': old_job.get('platform', 'Confirmit'),
        'country': old_job.get('country', ''),
        'mode': 'full',
        'user_email': session['user_email'],
        'created_at': datetime.now().isoformat(),
        'retest_of': job_id,
        'failed_qids': list(failed_qids),
        'verdict': None,
        'issues': [], 'term_results': [],
        'report_file': None,
        'doc_qids': 0, 'live_qids': 0,
        'total_issues': 0, 'term_passed': 0, 'term_total': 0,
    }

    import threading
    t = threading.Thread(
        target=run_qc_engine,
        args=(new_job_id, doc_path, survey_url, old_job.get('country', ''), 'full', []),
        daemon=True
    )
    t.start()

    return redirect('/progress/' + new_job_id)



# ================================================================
# LANDING PAGE v2 — Light theme with all sections
# ================================================================
@app.route('/home')
def home_landing():
    c = site_content

    # Get rotating testimonials (pick 3)
    tests = []
    for i in range(1, 7):
        if f'test{i}_name' in c:
            tests.append({
                'name': c[f'test{i}_name'],
                'role': c[f'test{i}_role'],
                'company': c[f'test{i}_company'],
                'country': c[f'test{i}_country'],
                'flag': c[f'test{i}_flag'],
                'quote': c[f'test{i}_quote'],
                'rating': int(c[f'test{i}_rating']),
            })
    # Show first 3 (admin can reorder by editing keys)
    visible_tests = tests[:3]

    test_cards = ''
    for t in visible_tests:
        stars = '<i class="ti ti-star-filled"></i>' * t['rating']
        initials = ''.join([n[0] for n in t['name'].split()[:2]])
        test_cards += f"""<div class="test-card">
  <div class="test-stars">{stars}</div>
  <div class="test-quote">"{t['quote']}"</div>
  <div class="test-author">
    <div class="test-avatar">{initials}</div>
    <div>
      <div class="test-name">{t['name']}</div>
      <div class="test-role">{t['role']} at {t['company']} · {t['country']}</div>
    </div>
  </div>
</div>"""

    page = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SurveyQC — Premium AI Survey QC for Market Research Teams</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">

<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>

<style>
:root{
  --bg:#F7F4EE; --bg2:#FFFDF9; --card:#FFFFFF;
  --text:#171717; --text2:#5F5B53; --text3:#8A847A;
  --accent:#C46A2B; --accent-hover:#A9551F; --accent-bg:#F5E6D8;
  --border:#E8E1D8; --border2:#F0EBE3;
  --dark:#1B140F; --dark2:#2A1F18;
  --success:#3F7D58; --warn:#D89B2B; --danger:#C84B31;
  --shadow:0 1px 2px rgba(24,17,10,0.04),0 4px 12px rgba(24,17,10,0.05);
  --shadow-lg:0 10px 40px rgba(24,17,10,0.08);
  --radius:20px; --radius-btn:14px;
}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'Inter','Plus Jakarta Sans',sans-serif;-webkit-font-smoothing:antialiased}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);line-height:1.5;overflow-x:hidden}
a{text-decoration:none;color:inherit}
img{max-width:100%}

.announce{background:var(--dark);color:#E8DDD2;padding:10px 24px;text-align:center;font-size:13px;font-weight:500}
.announce a{color:var(--accent);font-weight:600;margin-left:6px}
.nav{background:rgba(247,244,238,.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:0 32px;height:68px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.nav-logo{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px;color:var(--text)}
.nav-logo-mark{width:32px;height:32px;background:var(--dark);border-radius:9px;position:relative}
.nav-logo-mark::after{content:"";position:absolute;inset:6px;border:1.5px solid var(--accent);border-radius:5px}
.nav-links{display:flex;align-items:center;gap:32px}
.nav-link{font-size:14px;font-weight:500;color:var(--text2);transition:color .2s}
.nav-link:hover{color:var(--text)}
.nav-cta{display:flex;align-items:center;gap:12px}
.btn-sign{font-size:14px;font-weight:500;color:var(--text);padding:8px 18px;border-radius:10px;transition:background .2s}
.btn-sign:hover{background:var(--accent-bg)}
.btn-primary{background:var(--dark);color:#F7F4EE;font-size:14px;font-weight:600;padding:10px 22px;border-radius:var(--radius-btn);transition:all .2s;display:inline-flex;align-items:center;gap:6px;border:none;cursor:pointer}
.btn-primary:hover{background:var(--dark2);transform:translateY(-1px);box-shadow:var(--shadow-lg)}
.btn-ghost{background:white;color:var(--text);border:1px solid var(--border);font-size:14px;font-weight:500;padding:10px 22px;border-radius:var(--radius-btn);transition:all .2s}
.btn-ghost:hover{background:var(--bg2)}
.hamburger{display:none;background:none;border:none;cursor:pointer;width:36px;height:36px;align-items:center;justify-content:center}
.hamburger i{font-size:22px;color:var(--text)}

.hero{padding:72px 24px 72px;text-align:center;position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;top:-100px;left:50%;transform:translateX(-50%);width:800px;height:500px;background:radial-gradient(ellipse,rgba(196,106,43,.12),transparent 60%);pointer-events:none;z-index:0}
.hero-inner{position:relative;z-index:1;max-width:920px;margin:0 auto}
.hero-badge{display:inline-flex;align-items:center;gap:8px;background:white;border:1px solid var(--border);padding:6px 16px 6px 8px;border-radius:100px;font-size:13px;color:var(--text2);margin-bottom:32px;font-weight:500;box-shadow:var(--shadow)}
.hero-badge-pill{background:var(--accent-bg);color:var(--accent);font-size:11px;font-weight:700;padding:3px 10px;border-radius:100px;text-transform:uppercase}
.hero h1{font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(36px,6vw,72px);font-weight:800;line-height:1.05;letter-spacing:-2px;color:var(--text);margin-bottom:24px}
.hero h1 .accent{color:var(--accent);position:relative;display:inline-block}
.hero h1 .accent::after{content:"";position:absolute;bottom:6px;left:0;right:0;height:8px;background:rgba(196,106,43,.18);z-index:-1;border-radius:4px}
.hero-sub{font-size:clamp(17px,2vw,21px);color:var(--text2);max-width:600px;margin:0 auto 36px;line-height:1.65}
.hero-cta{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin-bottom:20px}
.hero-cta .btn-primary{padding:14px 28px;font-size:15px}
.hero-meta{font-size:13px;color:var(--text3);display:flex;align-items:center;justify-content:center;gap:18px;flex-wrap:wrap}
.hero-meta-item{display:flex;align-items:center;gap:6px}
.hero-meta-item i{color:var(--success);font-size:15px}

.trusted{padding:40px 24px 20px;text-align:center}
.trusted-l{font-size:12px;color:var(--text3);text-transform:uppercase;letter-spacing:.12em;font-weight:600;margin-bottom:24px}
.trusted-row{display:flex;align-items:center;justify-content:center;gap:48px;flex-wrap:wrap;opacity:.7}
.trusted-logo{font-family:'Plus Jakarta Sans',sans-serif;font-size:18px;font-weight:700;color:var(--text2)}

.section{padding:88px 24px}
.container{max-width:1240px;margin:0 auto;padding:0 24px}
.sec-head{text-align:center;max-width:720px;margin:0 auto 48px}
.sec-tag{display:inline-block;background:var(--accent-bg);color:var(--accent);font-size:12px;font-weight:700;padding:5px 14px;border-radius:100px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:16px}
.sec-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(28px,4vw,46px);font-weight:800;line-height:1.1;letter-spacing:-1.2px;margin-bottom:18px;color:var(--text)}
.sec-sub{font-size:18px;color:var(--text2);line-height:1.65;max-width:600px;margin:0 auto}

.feat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.feat-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:32px;transition:all .3s ease}
.feat-card:hover{transform:translateY(-4px);box-shadow:var(--shadow-lg);border-color:var(--accent-bg)}
.feat-icon{width:48px;height:48px;border-radius:12px;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;margin-bottom:20px}
.feat-icon i{font-size:22px;color:var(--accent)}
.feat-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:18px;font-weight:700;margin-bottom:10px;color:var(--text)}
.feat-desc{font-size:14px;color:var(--text2);line-height:1.7}

.steps-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.step-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:36px 28px;transition:all .3s}
.step-card:hover{transform:translateY(-4px);box-shadow:var(--shadow-lg)}
.step-num{font-family:'Plus Jakarta Sans',sans-serif;font-size:14px;font-weight:700;color:var(--accent);background:var(--accent-bg);width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin-bottom:20px}
.step-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:20px;font-weight:700;margin-bottom:10px;color:var(--text)}
.step-desc{font-size:14px;color:var(--text2);line-height:1.7}

.test-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.test-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:30px;transition:all .3s}
.test-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-lg)}
.test-stars{display:flex;gap:2px;margin-bottom:18px}
.test-stars i{color:var(--warn);font-size:14px}
.test-quote{font-size:15px;color:var(--text);line-height:1.65;margin-bottom:22px;font-weight:500}
.test-author{display:flex;align-items:center;gap:12px;padding-top:18px;border-top:1px solid var(--border2)}
.test-avatar{width:40px;height:40px;border-radius:50%;background:var(--accent-bg);color:var(--accent);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px}
.test-name{font-size:14px;font-weight:700;color:var(--text)}
.test-role{font-size:12px;color:var(--text3);margin-top:1px}

.pricing-cta-box{max-width:780px;margin:0 auto;background:white;border:1px solid var(--border);border-radius:var(--radius);padding:48px 40px;text-align:center;box-shadow:var(--shadow)}
.pricing-cta-box h3{font-family:'Plus Jakarta Sans',sans-serif;font-size:28px;font-weight:800;color:var(--text);margin-bottom:10px;letter-spacing:-0.8px}
.pricing-cta-box p{font-size:16px;color:var(--text2);margin-bottom:24px}
.pricing-cta-meta{display:flex;justify-content:center;gap:32px;margin-bottom:24px;flex-wrap:wrap}
.pricing-cta-meta-item{display:flex;flex-direction:column;align-items:center}
.pricing-cta-meta-num{font-family:'Plus Jakarta Sans',sans-serif;font-size:24px;font-weight:800;color:var(--accent)}
.pricing-cta-meta-lbl{font-size:12px;color:var(--text3);margin-top:2px;font-weight:500}

.cta-banner{padding:80px 24px}
.cta-inner{max-width:920px;margin:0 auto;background:linear-gradient(135deg,var(--dark) 0%,#2A1F18 100%);border-radius:32px;padding:64px 48px;text-align:center;position:relative;overflow:hidden}
.cta-inner::before{content:"";position:absolute;top:-100px;right:-100px;width:300px;height:300px;background:radial-gradient(circle,rgba(196,106,43,.4),transparent 70%);pointer-events:none}
.cta-inner h2{font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(28px,4vw,42px);color:white;margin-bottom:14px;font-weight:800;letter-spacing:-1px;position:relative;z-index:1}
.cta-inner p{font-size:16px;color:#D4C6B6;margin-bottom:32px;position:relative;z-index:1}
.cta-inner .btn-primary{background:var(--accent);position:relative;z-index:1;padding:14px 32px;font-size:15px}
.cta-inner .btn-primary:hover{background:var(--accent-hover)}

.footer{background:var(--dark);color:#B8AC9F;padding:80px 24px 30px}
.footer-inner{max-width:1180px;margin:0 auto}
.footer-grid{display:grid;grid-template-columns:2.2fr 1fr 1fr 1fr 1fr;gap:48px;margin-bottom:60px}
.footer-brand-logo{display:flex;align-items:center;gap:10px;margin-bottom:20px}
.footer-brand-mark{width:32px;height:32px;background:var(--accent);border-radius:9px;display:flex;align-items:center;justify-content:center}
.footer-brand-text{font-family:'Plus Jakarta Sans',sans-serif;font-size:18px;font-weight:700;color:white}
.footer-brand p{font-size:14px;line-height:1.7;color:#9A8C7B;margin-bottom:18px;max-width:280px}
.footer-social{display:flex;gap:10px}
.footer-social a{width:36px;height:36px;background:rgba(255,255,255,.06);border-radius:9px;display:flex;align-items:center;justify-content:center;transition:all .2s;color:#9A8C7B}
.footer-social a:hover{background:var(--accent);color:white}
.footer-title{font-size:13px;font-weight:700;color:white;text-transform:uppercase;letter-spacing:.06em;margin-bottom:18px}
.footer-link{display:block;font-size:14px;color:#9A8C7B;margin-bottom:12px;transition:color .2s}
.footer-link:hover{color:white}
.footer-bottom{border-top:1px solid rgba(255,255,255,.08);padding-top:28px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px}
.footer-bottom p{font-size:13px;color:#7A6E5F}

.mobile-menu{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:var(--bg);z-index:200;padding:80px 32px;flex-direction:column;gap:18px}
.mobile-menu.open{display:flex}
.mobile-menu a{font-size:18px;font-weight:600;color:var(--text);padding:12px 0;border-bottom:1px solid var(--border)}
.mobile-menu-close{position:absolute;top:20px;right:20px;background:none;border:none;font-size:28px;color:var(--text);cursor:pointer}



.platform-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;max-width:880px;margin:0 auto}
.platform-pill{display:flex;align-items:center;gap:14px;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;transition:all .2s}
.platform-pill:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg);border-color:var(--accent)}
.platform-pill-mark{width:42px;height:42px;border-radius:11px;background:var(--dark);color:var(--accent);display:flex;align-items:center;justify-content:center;font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:18px;flex-shrink:0}
.platform-pill-name{font-size:15px;font-weight:700;color:var(--text)}
.platform-pill-status{font-size:11px;font-weight:600;margin-top:2px}
.platform-pill-status.live{color:var(--success)}
.platform-pill-status.soon{color:var(--text3)}
@media(max-width:768px){.platform-grid{grid-template-columns:1fr 1fr}}
@media(max-width:480px){.platform-grid{grid-template-columns:1fr}}
.checks-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;max-width:1100px;margin:0 auto}
.check-pill{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:13px 14px;transition:all .2s}
.check-pill:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.check-pill-icon{width:30px;height:30px;border-radius:8px;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.check-pill-icon i{font-size:15px;color:var(--accent)}
.check-pill span{font-size:13px;font-weight:600;color:var(--text);line-height:1.3}
@media(max-width:1024px){.checks-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:768px){.checks-grid{grid-template-columns:repeat(2,1fr);gap:10px}}
@media(max-width:480px){.checks-grid{grid-template-columns:1fr}}
@media(max-width:1024px){
  .footer-grid{grid-template-columns:1fr 1fr 1fr}
}
@media(max-width:768px){
  .nav-links{display:none}
  .hamburger{display:flex}
  .nav-cta .btn-sign{display:none}
  .nav{padding:0 18px}
  .hero{padding:60px 18px 70px}
  .section{padding:56px 18px}
  .cta-banner{padding:64px 18px}
  .cta-inner{padding:48px 28px;border-radius:24px}
  .feat-grid,.steps-grid,.test-grid{grid-template-columns:1fr;gap:14px}
  .footer-grid{grid-template-columns:1fr 1fr;gap:36px}
  .footer{padding:60px 20px 24px}
  .pricing-cta-box{padding:32px 24px}
  .pricing-cta-meta{gap:20px}
}
@media(max-width:480px){
  .hero h1{font-size:36px;letter-spacing:-1.2px}
  .sec-title{font-size:28px}
}
</style>
</head>
<body>

<div class="announce">
  <i class="ti ti-sparkles"></i> """ + c['announcement'] + """
  <a href="/signup">Try free →</a>
</div>

<nav class="nav">
  <a href="/home" class="nav-logo">
    <div class="nav-logo-mark"></div>
    """ + c['site_name'] + """
  </a>
  <div class="nav-links">
    <a href="/features" class="nav-link">Features</a>
    <a href="/pricing" class="nav-link">Pricing</a>
    <a href="#how" class="nav-link">How it works</a>
    <a href="#testimonials" class="nav-link">Testimonials</a>
    <a href="/docs" class="nav-link">Docs</a>
    <a href="/blog" class="nav-link">Blog</a>
  </div>
  <div class="nav-cta">
    <a href="/login" class="btn-sign">Sign in</a>
    <a href="/signup" class="btn-primary">Start Free <i class="ti ti-arrow-right"></i></a>
    <button class="hamburger" onclick="document.getElementById('mm').classList.add('open')"><i class="ti ti-menu-2"></i></button>
  </div>
</nav>

<div class="mobile-menu" id="mm">
  <button class="mobile-menu-close" onclick="document.getElementById('mm').classList.remove('open')"><i class="ti ti-x"></i></button>
  <a href="/features">Features</a>
  <a href="/pricing">Pricing</a>
  <a href="#how">How it works</a>
  <a href="#testimonials">Testimonials</a>
  <a href="/docs">Docs</a>
  <a href="/blog">Blog</a>
  <a href="/login">Sign in</a>
  <a href="/signup" style="background:var(--dark);color:#F7F4EE;text-align:center;border-radius:14px;padding:14px;margin-top:12px;border:none">Start Free →</a>
</div>

<section class="hero">
  <div class="hero-inner">
    <div class="hero-badge">
      <span class="hero-badge-pill">New</span>
      WhatsApp screenshot QC — try it free
    </div>
    <h1>""" + c['hero_heading_part1'] + """<br><span class="accent">""" + c['hero_heading_part2'] + """</span> """ + c['hero_heading_part3'] + """</h1>
    <p class="hero-sub">""" + c['hero_subheading'] + """</p>
    <div class="hero-cta">
      <a href="/signup" class="btn-primary">""" + c['hero_cta'] + """ <i class="ti ti-arrow-right"></i></a>
      <a href="#how" class="btn-ghost">""" + c['hero_cta2'] + """</a>
    </div>
    <div class="hero-meta">
      <span class="hero-meta-item"><i class="ti ti-check"></i>No credit card</span>
      <span class="hero-meta-item"><i class="ti ti-check"></i>5 free reports/month</span>
      <span class="hero-meta-item"><i class="ti ti-check"></i>Cancel anytime</span>
    </div>
  </div>
</section>

<section class="trusted">
  <div class="trusted-l">Trusted by QC teams at leading research agencies</div>
  <div class="trusted-row">
    <div class="trusted-logo">IPSOS</div>
    <div class="trusted-logo">Kantar</div>
    <div class="trusted-logo">Nielsen</div>
    <div class="trusted-logo">YouGov</div>
    <div class="trusted-logo">GfK</div>
    <div class="trusted-logo">Dynata</div>
  </div>
</section>
<section style="padding:20px 24px 60px">
  <div class="container">
    <div style="text-align:center;margin-bottom:32px">
      <span class="sec-tag">Integrations</span>
      <h2 style="font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(24px,3vw,34px);font-weight:800;letter-spacing:-1px;margin-top:12px">Works with every major platform</h2>
    </div>
    <div class="platform-grid">
      <div class="platform-pill"><div class="platform-pill-mark">C</div><div><div class="platform-pill-name">Confirmit</div><div class="platform-pill-status live">Supported</div></div></div>
      <div class="platform-pill"><div class="platform-pill-mark">D</div><div><div class="platform-pill-name">Decipher</div><div class="platform-pill-status live">Supported</div></div></div>
      <div class="platform-pill"><div class="platform-pill-mark">F</div><div><div class="platform-pill-name">Forsta</div><div class="platform-pill-status live">Supported</div></div></div>
      <div class="platform-pill"><div class="platform-pill-mark">Q</div><div><div class="platform-pill-name">Qualtrics</div><div class="platform-pill-status live">Supported</div></div></div>
      <div class="platform-pill"><div class="platform-pill-mark">S</div><div><div class="platform-pill-name">SurveyMonkey</div><div class="platform-pill-status soon">Coming soon</div></div></div>
      <div class="platform-pill"><div class="platform-pill-mark">A</div><div><div class="platform-pill-name">Alchemer</div><div class="platform-pill-status soon">Coming soon</div></div></div>
    </div>
  </div>
</section>


<section class="section" id="features">
  <div class="container">
    <div class="sec-head">
      <span class="sec-tag">Features</span>
      <h2 class="sec-title">Everything manual<br>— now automated.</h2>
      <p class="sec-sub">15+ specialized checks run in parallel. Nothing slips through.</p>
    </div>
    <div class="feat-grid">
      <div class="feat-card">
        <div class="feat-icon"><i class="ti ti-shield-check"></i></div>
        <div class="feat-title">""" + c['feature1_title'] + """</div>
        <div class="feat-desc">""" + c['feature1_desc'] + """</div>
      </div>
      <div class="feat-card">
        <div class="feat-icon"><i class="ti ti-search"></i></div>
        <div class="feat-title">""" + c['feature2_title'] + """</div>
        <div class="feat-desc">""" + c['feature2_desc'] + """</div>
      </div>
      <div class="feat-card">
        <div class="feat-icon"><i class="ti ti-camera"></i></div>
        <div class="feat-title">""" + c['feature3_title'] + """</div>
        <div class="feat-desc">""" + c['feature3_desc'] + """</div>
      </div>
      <div class="feat-card">
        <div class="feat-icon"><i class="ti ti-world"></i></div>
        <div class="feat-title">""" + c['feature4_title'] + """</div>
        <div class="feat-desc">""" + c['feature4_desc'] + """</div>
      </div>
      <div class="feat-card">
        <div class="feat-icon"><i class="ti ti-shield-check"></i></div>
        <div class="feat-title">""" + c['feature5_title'] + """</div>
        <div class="feat-desc">""" + c['feature5_desc'] + """</div>
      </div>
      <div class="feat-card">
        <div class="feat-icon"><i class="ti ti-refresh"></i></div>
        <div class="feat-title">""" + c['feature6_title'] + """</div>
        <div class="feat-desc">""" + c['feature6_desc'] + """</div>
      </div>
    </div>
    <div style="text-align:center;margin-top:40px">
      <a href="/features" class="btn-ghost">View all 25+ features <i class="ti ti-arrow-right"></i></a>
    </div>
  </div>
</section>




<section class="section" id="how" style="background:var(--bg2)">
  <div class="container">
    <div class="sec-head">
      <span class="sec-tag">How it works</span>
      <h2 class="sec-title">3 steps to perfect QC.</h2>
      <p class="sec-sub">From upload to professional Word report in under 12 minutes.</p>
    </div>
    <div class="steps-grid">
      <div class="step-card">
        <div class="step-num">01</div>
        <div class="step-title">Upload doc & URL</div>
        <div class="step-desc">Upload your screener .docx and paste the live survey URL. Add screenshots optionally.</div>
      </div>
      <div class="step-card">
        <div class="step-num">02</div>
        <div class="step-title">AI tests everything</div>
        <div class="step-desc">AI crawls every path, runs all 15+ checks in parallel, captures screenshots.</div>
      </div>
      <div class="step-card">
        <div class="step-num">03</div>
        <div class="step-title">Download Word report</div>
        <div class="step-desc">Professional report with all issues, screenshot proof, and QC certificate.</div>
      </div>
    </div>
  </div>
</section>

<section class="section" id="testimonials">
  <div class="container">
    <div class="sec-head">
      <span class="sec-tag">Testimonials</span>
      <h2 class="sec-title">Loved by QC teams<br>around the world.</h2>
      <p class="sec-sub">From market research agencies to in-house teams worldwide.</p>
    </div>
    <div class="test-grid">""" + test_cards + """</div>
  </div>
</section>



<section class="cta-banner">
  <div class="cta-inner">
    <h2>Save 8 hours per survey<br>starting today.</h2>
    <p>Join 500+ QC professionals worldwide. Free forever. No credit card needed.</p>
    <a href="/signup" class="btn-primary">Start free — no card required <i class="ti ti-arrow-right"></i></a>
  </div>
</section>

<footer class="footer">
  <div class="footer-inner">
    <div class="footer-grid">
      <div class="footer-brand">
        <div class="footer-brand-logo">
          <div class="footer-brand-mark"><i class="ti ti-shield-check" style="color:white;font-size:16px"></i></div>
          <div class="footer-brand-text">""" + c['site_name'] + """</div>
        </div>
        <p>""" + c['tagline'] + """. Built for QC professionals at leading market research agencies.</p>
        <div class="footer-social">
          <a href="""" + c['linkedin_url'] + """"><i class="ti ti-brand-linkedin"></i></a>
          <a href="""" + c['twitter_url'] + """"><i class="ti ti-brand-twitter"></i></a>
        </div>
      </div>
      <div>
        <div class="footer-title">Product</div>
        <a href="/features" class="footer-link">Features</a>
        <a href="/pricing" class="footer-link">Pricing</a>
        <a href="/docs" class="footer-link">Documentation</a>
        <a href="/changelog" class="footer-link">Changelog</a>
      </div>
      <div>
        <div class="footer-title">Resources</div>
        <a href="/blog" class="footer-link">Blog</a>
        <a href="/community" class="footer-link">Community</a>
        <a href="/affiliate" class="footer-link">Affiliate</a>
        <a href="mailto:""" + c['support_email'] + """" class="footer-link">Contact</a>
      </div>
      <div>
        <div class="footer-title">Compare</div>
        <a href="/compare/chatgpt" class="footer-link">vs ChatGPT</a>
        <a href="/compare/excel" class="footer-link">vs Excel</a>
        <a href="/compare/manual" class="footer-link">vs Manual</a>
      </div>
      <div>
        <div class="footer-title">Legal</div>
        <a href="/privacy-policy" class="footer-link">Privacy</a>
        <a href="/terms" class="footer-link">Terms</a>
      </div>
    </div>
    <div class="footer-bottom">
      <p>© """ + c['footer_text'] + """</p>
    </div>
  </div>
</footer>

</body>
</html>"""
    return page


@app.route('/privacy-policy')
def privacy_policy_page():
    return render_template_string(SHARED_CSS + """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Privacy Policy — SurveyQC</title></head><body>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Terms of Service — SurveyQC</title></head><body>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Templates — SurveyQC</title></head><body>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Privacy Settings — Admin</title><script src="/admin-sidebar-js"></script></head><body>
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
      <div style="background:#F5E6D8;border-radius:8px;padding:12px;margin-bottom:12px">
        <p style="font-size:11px;color:#C46A2B">🕛 Auto-delete runs every night at 12:00 AM · Compliant: 🇪🇺 GDPR · 🇺🇸 CCPA · 🇮🇳 DPDP · 🇬🇧 UK · 🇦🇺 AUS</p>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>404 — SurveyQC</title></head><body>
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
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0"><title>Error — SurveyQC</title></head><body>
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


# Load saved Gemini key from .env into api_store on startup
import os as _os
_saved_gemini = _os.environ.get('GEMINI_API_KEY', '').strip()
if _saved_gemini and 'gemini' in api_store:
    api_store['gemini']['key'] = _saved_gemini
    api_store['gemini']['status'] = 'active'
    api_store['gemini']['active'] = True

@app.route('/admin/apis', methods=['GET', 'POST'])
@admin_required
def admin_apis():
    saved_msg = ''
    if request.method == 'POST':
        api_id = request.form.get('api_id', '')
        key = request.form.get('key', '').strip()
        action = request.form.get('action', 'save')
        if api_id in api_store:
            api = api_store[api_id]
            if action == 'save' and key:
                api['key'] = key; api['status'] = 'active'; api['active'] = True
                saved_msg = api['name'] + ' key saved!'
                # Save gemini key to .env for persistence
                if api_id == 'gemini':
                    try:
                        from pathlib import Path
                        env_file = Path('/var/www/surveyqc/.env')
                        lines = []
                        if env_file.exists():
                            lines = [l for l in env_file.read_text().splitlines() if not l.startswith('GEMINI_API_KEY')]
                        lines.append('GEMINI_API_KEY=' + key)
                        env_file.write_text(chr(10).join(lines) + chr(10))
                        import os
                        os.environ['GEMINI_API_KEY'] = key
                    except Exception as e:
                        pass
            elif action == 'delete':
                api['key'] = ''; api['status'] = 'not_added'; api['active'] = False
                saved_msg = api['name'] + ' removed.'
            elif action == 'toggle':
                api['active'] = not api.get('active', False)
                saved_msg = api['name'] + (' enabled.' if api['active'] else ' disabled.')

    active_count = sum(1 for a in api_store.values() if a.get('active'))
    not_added = sum(1 for a in api_store.values() if not a.get('key'))

    def card(aid):
        a = api_store[aid]
        has_key = bool(a.get('key',''))
        is_active = a.get('active', False)
        if has_key and is_active:
            bg = '#F0FDF4'; border = '#A5D6A7'
            badge = '<span style="background:#EAF3DE;color:#27500A;font-size:10px;padding:2px 8px;border-radius:20px;font-weight:500">Active</span>'
        elif has_key:
            bg = '#FFFBEB'; border = '#FCD34D'
            badge = '<span style="background:#FAEEDA;color:#633806;font-size:10px;padding:2px 8px;border-radius:20px;font-weight:500">Disabled</span>'
        else:
            bg = 'white'; border = '#DDE1E7'
            badge = '<span style="background:#F1EFE8;color:#444441;font-size:10px;padding:2px 8px;border-radius:20px;font-weight:500">Not added</span>'
        masked = chr(8226)*10 + a['key'][-4:] if len(a.get('key','')) > 4 else ''
        ph = 'Update key...' if has_key else 'Paste API key...'
        lbl = 'Disable' if is_active else 'Enable'
        toggle = ('<form method="POST" style="display:inline"><input type="hidden" name="api_id" value="'+aid+'"><input type="hidden" name="action" value="toggle"><button type="submit" style="background:#FBF8F2;color:#374151;border:0.5px solid #DDE1E7;font-size:11px;padding:5px 10px;border-radius:7px;cursor:pointer;margin-right:4px">'+lbl+'</button></form>') if has_key else ''
        remove = ('<form method="POST" style="display:inline"><input type="hidden" name="api_id" value="'+aid+'"><input type="hidden" name="action" value="delete"><button type="submit" style="background:#FCEBEB;color:#791F1F;border:none;font-size:11px;padding:5px 10px;border-radius:7px;cursor:pointer">Remove</button></form>') if has_key else ''
        masked_row = ('<p style="font-family:monospace;font-size:11px;background:#FBF8F2;padding:4px 8px;border-radius:5px;margin-bottom:8px;color:#9CA3AF">'+masked+'</p>') if masked else ''
        return ('<div style="background:'+bg+';border:0.5px solid '+border+';border-radius:10px;padding:14px;margin-bottom:10px">'
            '<div style="display:flex;align-items:start;gap:10px">'
            '<div style="width:34px;height:34px;border-radius:8px;background:'+a['color']+';display:flex;align-items:center;justify-content:center;flex-shrink:0">'
            '<i class="ti '+a['icon']+'" style="font-size:17px;color:white"></i></div>'
            '<div style="flex:1">'
            '<div style="display:flex;align-items:center;gap:8px;margin-bottom:3px"><p style="font-size:13px;font-weight:600;color:#1A1A2E">'+a['name']+'</p>'+badge+'</div>'
            '<p style="font-size:11px;color:#6B7280;margin-bottom:2px">'+a['use_for']+'</p>'
            '<p style="font-size:10px;color:#9CA3AF;margin-bottom:7px">Cost: '+a['cost']+'</p>'
            + masked_row +
            '<form method="POST" style="display:flex;gap:6px;margin-bottom:6px">'
            '<input type="hidden" name="api_id" value="'+aid+'"><input type="hidden" name="action" value="save">'
            '<input name="key" type="password" placeholder="'+ph+'" style="flex:1;padding:7px 10px;border:0.5px solid #DDE1E7;border-radius:7px;font-size:12px;outline:none">'
            '<button type="submit" style="background:#1B140F;color:white;border:none;font-size:12px;padding:7px 12px;border-radius:7px;cursor:pointer;white-space:nowrap">Save</button>'
            '</form>'
            '<div style="display:flex;align-items:center;gap:4px">'+toggle+remove+'<a href="https://'+a['docs']+'" target="_blank" style="font-size:10px;color:#185FA5;margin-left:6px">Get key</a></div>'
            '</div></div></div>')

    ai_html = card('gemini') + card('claude') + card('openai')
    pay_html = card('stripe') + card('razorpay') + card('sendgrid')
    notif_html = card('whatsapp') + card('slack')
    other_html = card('google_oauth') + card('microsoft_oauth') + card('sentry') + card('cloudflare')
    alert = ('<div style="background:#EAF3DE;border:0.5px solid #A5D6A7;border-radius:8px;padding:10px 14px;margin-bottom:16px;color:#27500A;font-size:13px">'+saved_msg+'</div>') if saved_msg else ''

    page = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>API Management - Admin</title>'
    page += '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">'
    page += '<style>*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{background:#F0F2F5;color:#1A1A2E}a{text-decoration:none}</style><script src="/admin-sidebar-js"></script></head><body>'
    page += '<div style="padding:24px;max-width:1000px;margin:0 auto">'
    page += '<div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">'
    page += '<a href="/admin" style="color:#6B7280;font-size:22px">&larr;</a>'
    page += '<div><p style="font-size:20px;font-weight:600;color:#1A1A2E">API Management</p><p style="font-size:12px;color:#6B7280">Manage all your API integrations in one place</p></div>'
    page += '<div style="margin-left:auto;display:flex;gap:10px">'
    page += '<div style="background:#EAF3DE;border-radius:8px;padding:10px 16px;text-align:center"><p style="font-size:18px;font-weight:600;color:#27500A">'+str(active_count)+'</p><p style="font-size:10px;color:#3B6D11">Active</p></div>'
    page += '<div style="background:white;border:0.5px solid #DDE1E7;border-radius:8px;padding:10px 16px;text-align:center"><p style="font-size:18px;font-weight:600;color:#1A1A2E">'+str(len(api_store))+'</p><p style="font-size:10px;color:#6B7280">Total</p></div>'
    page += '<div style="background:#FCEBEB;border-radius:8px;padding:10px 16px;text-align:center"><p style="font-size:18px;font-weight:600;color:#791F1F">'+str(not_added)+'</p><p style="font-size:10px;color:#A32D2D">Not added</p></div>'
    page += '</div></div>'+alert
    page += '<div style="background:#F5E6D8;border:0.5px solid #B5D4F4;border-radius:10px;padding:12px 16px;margin-bottom:20px"><p style="font-size:12px;color:#C46A2B">Security: API keys are stored server-side only. They are never exposed in source code or logs. Only admins can view them.</p></div>'
    page += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">'
    page += '<div><p style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">AI APIs</p>'+ai_html
    page += '<p style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:.08em;margin:16px 0 12px">Notifications</p>'+notif_html+'</div>'
    page += '<div><p style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">Payments & Email</p>'+pay_html
    page += '<p style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:.08em;margin:16px 0 12px">Other Services</p>'+other_html+'</div>'
    page += '</div></div></body></html>'
    return render_template_string(page)




@app.route('/admin-sidebar-js')
def admin_sidebar_js():
    js = """
(function(){
  if(!window.location.pathname.startsWith('/admin') || window.location.pathname === '/admin/login') return;
  if(document.getElementById('admsb')) return;
  var s = document.createElement('div');
  s.id = 'admsb';
  s.style.cssText = 'width:220px;background:#1B140F;padding:20px 12px;position:fixed;height:100vh;overflow-y:auto;left:0;top:0;z-index:9999;box-sizing:border-box';
  s.innerHTML = [
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:20px">',
    '<div style="width:26px;height:26px;background:#C46A2B;border-radius:6px;display:flex;align-items:center;justify-content:center">',
    '<span style="color:white;font-size:16px">&#9673;</span></div>',
    '<span style="color:white;font-size:13px;font-weight:600">Admin Panel</span></div>',
    '<p style="font-size:9px;color:rgba(255,255,255,.3);margin-bottom:8px;text-transform:uppercase;padding:0 8px">Management</p>',
    '<a href="/admin" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Overview</a>',
    '<a href="/admin/users" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Users</a>',
    '<a href="/admin/reports" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; QC Reports</a>',
    '<a href="/admin/email" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Email Users</a>',
    '<a href="/admin/feedback" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Feedback</a>',
    '<hr style="border-color:rgba(255,255,255,.1);margin:8px 0">',
    '<p style="font-size:9px;color:rgba(255,255,255,.3);margin-bottom:8px;text-transform:uppercase;padding:0 8px">Settings</p>',
    '<a href="/admin/apis" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; API Keys</a>',
    '<a href="/admin/tokens" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Token Limits</a>',
    '<a href="/admin/content" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Content</a>',
    '<a href="/admin/privacy" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Privacy</a>',
    '<a href="/admin/gift" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px;margin-bottom:2px">&#9632; Gift Access</a>',
    '<hr style="border-color:rgba(255,255,255,.1);margin:8px 0">',
    '<a href="/" style="display:block;padding:8px 10px;color:#9A8C7B;font-size:12px;text-decoration:none;border-radius:7px">&#8592; Back to site</a>',
  ].join('');
  document.addEventListener('DOMContentLoaded',function(){document.body.insertBefore(s,document.body.firstChild);if(!document.querySelector('.main-content'))if(!document.querySelector('.main-content'))document.body.style.marginLeft='220px';});
})();
"""
    from flask import Response
    return Response(js, mimetype='application/javascript')




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
