"""
================================================================
  SURVEY QC TOOL — Web UI v1.0
  
  Install:
    pip3 install flask playwright python-docx
    playwright install chromium
    
  Run:
    python3 app.py
    
  Open browser:
    http://localhost:5000
================================================================
"""

from flask import Flask, render_template_string, request, send_file, jsonify
import os, re, sys, threading, json, uuid
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
UPLOAD_FOLDER = './uploads'
OUTPUT_FOLDER = './qc_output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Job status tracker
jobs = {}

# ================================================================
# HTML TEMPLATE
# ================================================================
HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SurveyQC — AI-powered Survey Testing</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #F5F5F0; color: #1a1a1a; min-height: 100vh; }
  
  .nav { background: #042C53; padding: 0 2rem; height: 56px; display: flex; align-items: center; justify-content: space-between; }
  .nav-logo { display: flex; align-items: center; gap: 10px; }
  .nav-logo-icon { width: 28px; height: 28px; background: #378ADD; border-radius: 6px; display: flex; align-items: center; justify-content: center; }
  .nav-logo-icon svg { width: 16px; height: 16px; fill: white; }
  .nav-title { color: white; font-size: 15px; font-weight: 500; }
  .nav-badge { background: rgba(255,255,255,0.15); color: #B5D4F4; font-size: 11px; padding: 3px 10px; border-radius: 20px; }
  
  .main { max-width: 800px; margin: 0 auto; padding: 2rem 1rem; }
  
  .hero { text-align: center; margin-bottom: 2rem; }
  .hero h1 { font-size: 28px; font-weight: 500; color: #042C53; margin-bottom: 8px; }
  .hero p { font-size: 14px; color: #666; line-height: 1.6; max-width: 500px; margin: 0 auto; }
  
  .card { background: white; border: 0.5px solid #e5e5e0; border-radius: 12px; padding: 24px; margin-bottom: 16px; }
  .card-title { font-size: 13px; font-weight: 500; color: #333; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  .card-title svg { width: 16px; height: 16px; }
  
  .upload-zone { border: 1.5px dashed #d0d0c8; border-radius: 8px; padding: 24px; text-align: center; cursor: pointer; transition: all 0.2s; background: #fafaf8; }
  .upload-zone:hover { border-color: #378ADD; background: #EBF5FF; }
  .upload-zone.active { border-color: #378ADD; background: #EBF5FF; }
  .upload-zone svg { width: 32px; height: 32px; fill: #999; margin-bottom: 10px; }
  .upload-zone p { font-size: 13px; color: #666; }
  .upload-zone span { font-size: 12px; color: #999; }
  .file-selected { background: #EAF3DE; border-color: #639922; }
  .file-selected svg { fill: #3B6D11; }
  .file-selected p { color: #27500A; font-weight: 500; }
  
  .screenshot-zone { border: 1.5px dashed #d0d0c8; border-radius: 8px; padding: 16px; text-align: center; cursor: pointer; background: #fafaf8; }
  .screenshot-zone:hover { border-color: #378ADD; background: #EBF5FF; }
  .screenshot-zone svg { width: 24px; height: 24px; fill: #999; margin-bottom: 6px; }
  .screenshot-zone p { font-size: 12px; color: #666; }
  
  input[type="file"] { display: none; }
  
  label { font-size: 12px; color: #555; margin-bottom: 5px; display: block; font-weight: 500; }
  input[type="text"], input[type="url"], select { width: 100%; padding: 9px 12px; border: 0.5px solid #d5d5d0; border-radius: 8px; font-size: 13px; color: #333; background: white; outline: none; transition: border 0.2s; }
  input[type="text"]:focus, input[type="url"]:focus, select:focus { border-color: #378ADD; box-shadow: 0 0 0 3px rgba(55,138,221,0.1); }
  
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
  
  .checks-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .check-item { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #444; }
  .check-item input[type="checkbox"] { width: 14px; height: 14px; cursor: pointer; accent-color: #042C53; }
  
  .btn-run { width: 100%; background: #042C53; color: white; border: none; padding: 13px; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; transition: background 0.2s; display: flex; align-items: center; justify-content: center; gap: 8px; }
  .btn-run:hover { background: #0A3D6F; }
  .btn-run:disabled { background: #99b0c5; cursor: not-allowed; }
  .btn-run svg { width: 16px; height: 16px; fill: white; }
  
  .progress-section { display: none; }
  .progress-section.visible { display: block; }
  
  .progress-card { background: white; border: 0.5px solid #e5e5e0; border-radius: 12px; padding: 24px; }
  .progress-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  .progress-title { font-size: 14px; font-weight: 500; color: #333; }
  .status-badge { font-size: 11px; padding: 3px 10px; border-radius: 20px; font-weight: 500; }
  .status-running { background: #E6F1FB; color: #0C447C; }
  .status-done { background: #EAF3DE; color: #27500A; }
  .status-error { background: #FCEBEB; color: #791F1F; }
  
  .progress-bar-wrap { background: #f0f0eb; border-radius: 4px; height: 6px; margin-bottom: 16px; overflow: hidden; }
  .progress-bar-fill { height: 100%; background: #042C53; border-radius: 4px; transition: width 0.5s ease; }
  
  .log-box { background: #1a1a2e; border-radius: 8px; padding: 14px; height: 200px; overflow-y: auto; font-family: 'SF Mono', 'Monaco', monospace; font-size: 11px; line-height: 1.6; }
  .log-line { margin-bottom: 2px; }
  .log-green { color: #5DCAA5; }
  .log-red { color: #FF6B6B; }
  .log-yellow { color: #FFD93D; }
  .log-blue { color: #74B9FF; }
  .log-white { color: #DFE6E9; }
  .log-cyan { color: #81ECEC; }
  
  .result-section { display: none; }
  .result-section.visible { display: block; }
  
  .result-card { background: white; border: 0.5px solid #e5e5e0; border-radius: 12px; padding: 24px; margin-bottom: 12px; }
  
  .verdict-pass { background: #EAF3DE; border: 1px solid #C0DD97; border-radius: 8px; padding: 14px 18px; display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .verdict-fail { background: #FCEBEB; border: 1px solid #F7C1C1; border-radius: 8px; padding: 14px 18px; display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .verdict-review { background: #FAEEDA; border: 1px solid #FAC775; border-radius: 8px; padding: 14px 18px; display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .verdict-text { font-size: 14px; font-weight: 500; }
  
  .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 16px; }
  .stat-box { background: #f8f8f4; border-radius: 8px; padding: 12px; text-align: center; }
  .stat-num { font-size: 22px; font-weight: 500; color: #1a1a1a; }
  .stat-label { font-size: 11px; color: #888; margin-top: 3px; }
  .stat-green .stat-num { color: #27500A; }
  .stat-red .stat-num { color: #791F1F; }
  .stat-blue .stat-num { color: #0C447C; }
  
  .issues-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .issues-table th { background: #042C53; color: white; padding: 8px 12px; text-align: left; font-weight: 500; }
  .issues-table td { padding: 8px 12px; border-bottom: 0.5px solid #f0f0eb; vertical-align: top; }
  .issues-table tr:last-child td { border-bottom: none; }
  .issues-table tr:hover td { background: #fafaf8; }
  .badge { display: inline-block; font-size: 10px; padding: 2px 8px; border-radius: 12px; font-weight: 500; }
  .badge-high { background: #FCEBEB; color: #791F1F; }
  .badge-medium { background: #FAEEDA; color: #633806; }
  .badge-info { background: #E6F1FB; color: #0C447C; }
  .badge-pass { background: #EAF3DE; color: #27500A; }
  .badge-fail { background: #FCEBEB; color: #791F1F; }
  .badge-manual { background: #FAEEDA; color: #633806; }
  
  .btn-download { background: #042C53; color: white; border: none; padding: 10px 20px; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; text-decoration: none; }
  .btn-download:hover { background: #0A3D6F; }
  .btn-download svg { width: 14px; height: 14px; fill: white; }
  
  .btn-new { background: none; border: 0.5px solid #d5d5d0; color: #333; padding: 10px 20px; border-radius: 8px; font-size: 13px; cursor: pointer; margin-left: 10px; }
  .btn-new:hover { background: #f5f5f0; }
  
  .time-saved { background: #042C53; border-radius: 10px; padding: 16px 20px; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; }
  .time-saved-left p { color: #85B7EB; font-size: 11px; margin-bottom: 4px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; }
  .time-saved-left h2 { color: white; font-size: 24px; font-weight: 500; }
  .time-saved-left span { color: #5DCAA5; font-size: 12px; font-weight: 500; }
  .time-saved-right { text-align: center; }
  .time-saved-right .roi { background: rgba(255,255,255,0.1); border-radius: 8px; padding: 8px 14px; }
  .time-saved-right .roi-num { color: white; font-size: 20px; font-weight: 500; }
  .time-saved-right .roi-label { color: #85B7EB; font-size: 10px; }
  
  .section-title { font-size: 13px; font-weight: 500; color: #333; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 0.5px solid #f0f0eb; }
  
  @media (max-width: 600px) {
    .grid-2, .grid-3 { grid-template-columns: 1fr; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .hero h1 { font-size: 22px; }
  }
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-logo">
    <div class="nav-logo-icon">
      <svg viewBox="0 0 24 24"><path d="M12 2L4 6v6c0 5.5 3.5 10.7 8 12 4.5-1.3 8-6.5 8-12V6L12 2z"/></svg>
    </div>
    <span class="nav-title">SurveyQC</span>
  </div>
  <span class="nav-badge">v8.0 · Any language · Any platform</span>
</nav>

<div class="main">
  
  <div class="hero" id="heroSection">
    <h1>Survey QC in 5 minutes</h1>
    <p>Upload your screener doc + paste survey URL. Tool automatically tests termination, text, words, piping and more.</p>
  </div>

  <!-- INPUT FORM -->
  <div id="inputSection">
    
    <div class="card">
      <div class="card-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="#042C53" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        Upload files
      </div>
      <div class="grid-2" style="margin-bottom:0">
        <div>
          <label>Screener document (.docx) *</label>
          <div class="upload-zone" id="docZone" onclick="document.getElementById('docFile').click()">
            <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zm-2 14H8v-2h4v2zm2-4H8v-2h6v2zm0-4H8V8h6v2z"/></svg>
            <p id="docLabel">Click to upload .docx</p>
            <span>Screener_Survey.docx</span>
          </div>
          <input type="file" id="docFile" accept=".docx" onchange="handleDocUpload(this)">
        </div>
        <div>
          <label>Screenshots (optional)</label>
          <div class="screenshot-zone" id="ssZone" onclick="document.getElementById('ssFiles').click()">
            <svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
            <p id="ssLabel">Add reference screenshots</p>
          </div>
          <input type="file" id="ssFiles" accept="image/*" multiple onchange="handleSSUpload(this)">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="#042C53" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
        Survey details
      </div>
      <div style="margin-bottom:12px">
        <label>Survey URL *</label>
        <input type="url" id="surveyUrl" placeholder="https://questionnaire.example.com/survey?id=123">
      </div>
      <div class="grid-3">
        <div>
          <label>Platform</label>
          <select id="platform">
            <option value="confirmit">Confirmit</option>
            <option value="decipher">Decipher</option>
            <option value="forsta">Forsta</option>
            <option value="qualtrics">Qualtrics</option>
          </select>
        </div>
        <div>
          <label>Country (if needed)</label>
          <input type="text" id="country" placeholder="Italy, France, Spain...">
        </div>
        <div>
          <label>Test mode</label>
          <select id="testMode">
            <option value="full">Full QC</option>
            <option value="quick">Quick test</option>
            <option value="logic">Logic only</option>
          </select>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="#042C53" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>
        Checks to run
      </div>
      <div class="checks-grid">
        <label class="check-item"><input type="checkbox" checked id="chk_termination"> Termination rules</label>
        <label class="check-item"><input type="checkbox" checked id="chk_text"> Question text match</label>
        <label class="check-item"><input type="checkbox" checked id="chk_words"> Missing words</label>
        <label class="check-item"><input type="checkbox" checked id="chk_mandatory"> Mandatory markers</label>
        <label class="check-item"><input type="checkbox" checked id="chk_options"> Options match</label>
        <label class="check-item"><input type="checkbox" checked id="chk_piping"> Piping markers</label>
        <label class="check-item"><input type="checkbox" checked id="chk_codes"> Answer codes</label>
        <label class="check-item"><input type="checkbox" checked id="chk_screenshots"> Auto screenshots</label>
      </div>
    </div>

    <button class="btn-run" id="runBtn" onclick="startQC()">
      <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
      Run QC analysis
    </button>

  </div>

  <!-- PROGRESS SECTION -->
  <div class="progress-section" id="progressSection">
    <div class="progress-card">
      <div class="progress-header">
        <span class="progress-title" id="progressTitle">Running QC analysis...</span>
        <span class="status-badge status-running" id="statusBadge">Running</span>
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" id="progressBar" style="width:0%"></div>
      </div>
      <div class="log-box" id="logBox"></div>
    </div>
  </div>

  <!-- RESULT SECTION -->
  <div class="result-section" id="resultSection">
    
    <div id="timeSavedBox" class="time-saved" style="display:none">
      <div class="time-saved-left">
        <p>Time saved this run</p>
        <h2 id="timeSavedHours">~8 hours</h2>
        <span>vs manual QC</span>
      </div>
      <div class="time-saved-right">
        <div class="roi">
          <div class="roi-num">🎉</div>
          <div class="roi-label">Done!</div>
        </div>
      </div>
    </div>

    <div class="result-card">
      <div id="verdictBox"></div>
      <div class="stats-grid" id="statsGrid"></div>
      <div style="display:flex;gap:8px;margin-top:4px">
        <a id="downloadBtn" class="btn-download" style="display:none">
          <svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Download report
        </a>
        <button class="btn-new" onclick="resetForm()">New QC</button>
      </div>
    </div>

    <div class="result-card" id="termResultCard" style="display:none">
      <div class="section-title">Termination test results</div>
      <table class="issues-table" id="termTable">
        <thead><tr><th>Status</th><th>QID</th><th>Code</th><th>Result</th></tr></thead>
        <tbody id="termBody"></tbody>
      </table>
    </div>

    <div class="result-card" id="issuesCard" style="display:none">
      <div class="section-title">Issues found</div>
      <table class="issues-table" id="issuesTable">
        <thead><tr><th>QID</th><th>Type</th><th>Severity</th><th>Details</th></tr></thead>
        <tbody id="issuesBody"></tbody>
      </table>
    </div>

    <div class="result-card" id="feedbackCard">
      <div class="section-title">Rate this report</div>
      <div id="stars" style="display:flex;gap:6px;margin-bottom:12px">
        <span style="font-size:24px;cursor:pointer" onclick="setRating(1)">⭐</span>
        <span style="font-size:24px;cursor:pointer" onclick="setRating(2)">⭐</span>
        <span style="font-size:24px;cursor:pointer" onclick="setRating(3)">⭐</span>
        <span style="font-size:24px;cursor:pointer" onclick="setRating(4)">⭐</span>
        <span style="font-size:24px;cursor:pointer" onclick="setRating(5)">⭐</span>
      </div>
      <input type="text" id="feedbackText" placeholder="Any comments? (optional)" style="margin-bottom:10px">
      <br>
      <button onclick="submitFeedback()" style="background:#042C53;color:white;border:none;padding:8px 18px;border-radius:6px;font-size:12px;cursor:pointer">Submit feedback</button>
      <button onclick="document.getElementById('feedbackCard').style.display='none'" style="background:none;border:none;padding:8px 14px;font-size:12px;color:#888;cursor:pointer">Skip</button>
    </div>

  </div>

</div>

<script>
let currentJobId = null;
let pollInterval = null;
let userRating = 0;
let docFilename = null;
let ssFilenames = [];

function handleDocUpload(input) {
  if (input.files[0]) {
    const zone = document.getElementById('docZone');
    zone.classList.add('file-selected');
    document.getElementById('docLabel').textContent = input.files[0].name;
  }
}

function handleSSUpload(input) {
  if (input.files.length > 0) {
    document.getElementById('ssLabel').textContent = input.files.length + ' screenshot(s) selected';
  }
}

function setRating(n) {
  userRating = n;
  const stars = document.getElementById('stars').children;
  for (let i = 0; i < 5; i++) {
    stars[i].style.opacity = i < n ? '1' : '0.3';
  }
}

function submitFeedback() {
  const comment = document.getElementById('feedbackText').value;
  fetch('/feedback', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rating: userRating, comment: comment})
  });
  document.getElementById('feedbackCard').innerHTML = '<p style="color:#27500A;font-size:13px">✅ Thank you for your feedback!</p>';
}

async function startQC() {
  const url = document.getElementById('surveyUrl').value.trim();
  const docFile = document.getElementById('docFile').files[0];

  if (!docFile) { alert('Please upload a .docx file'); return; }
  if (!url) { alert('Please enter survey URL'); return; }

  document.getElementById('runBtn').disabled = true;
  document.getElementById('inputSection').style.display = 'none';
  document.getElementById('heroSection').style.display = 'none';
  document.getElementById('progressSection').classList.add('visible');
  document.getElementById('resultSection').classList.remove('visible');

  const formData = new FormData();
  formData.append('doc', docFile);
  formData.append('url', url);
  formData.append('country', document.getElementById('country').value);
  formData.append('platform', document.getElementById('platform').value);
  formData.append('mode', document.getElementById('testMode').value);

  const ssFiles = document.getElementById('ssFiles').files;
  for (let f of ssFiles) formData.append('screenshots', f);

  try {
    const res = await fetch('/run', {method: 'POST', body: formData});
    const data = await res.json();
    if (data.job_id) {
      currentJobId = data.job_id;
      pollProgress();
    }
  } catch (e) {
    addLog('❌ Error starting QC: ' + e.message, 'red');
  }
}

function pollProgress() {
  pollInterval = setInterval(async () => {
    try {
      const res = await fetch('/status/' + currentJobId);
      const data = await res.json();
      
      updateProgress(data);
      
      if (data.status === 'done' || data.status === 'error') {
        clearInterval(pollInterval);
        if (data.status === 'done') showResults(data);
      }
    } catch (e) {}
  }, 1000);
}

function addLog(msg, color='white') {
  const box = document.getElementById('logBox');
  const line = document.createElement('div');
  line.className = 'log-line log-' + color;
  line.textContent = msg;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function updateProgress(data) {
  if (data.logs) {
    const box = document.getElementById('logBox');
    const currentCount = box.children.length;
    const newLogs = data.logs.slice(currentCount);
    newLogs.forEach(l => addLog(l.msg, l.color || 'white'));
  }
  
  if (data.progress !== undefined) {
    document.getElementById('progressBar').style.width = data.progress + '%';
  }
  
  if (data.phase) {
    document.getElementById('progressTitle').textContent = data.phase;
  }
  
  if (data.status === 'error') {
    document.getElementById('statusBadge').textContent = 'Error';
    document.getElementById('statusBadge').className = 'status-badge status-error';
  }
}

function showResults(data) {
  document.getElementById('progressSection').classList.remove('visible');
  document.getElementById('resultSection').classList.add('visible');
  document.getElementById('statusBadge').textContent = 'Done';
  document.getElementById('statusBadge').className = 'status-badge status-done';

  // Time saved
  const timeSaved = document.getElementById('timeSavedBox');
  timeSaved.style.display = 'flex';

  // Verdict
  const verdictBox = document.getElementById('verdictBox');
  let verdictClass, verdictText;
  if (data.verdict === 'PASS') {
    verdictClass = 'verdict-pass';
    verdictText = '✅ PASS — No critical issues found';
  } else if (data.verdict === 'FAIL') {
    verdictClass = 'verdict-fail';
    verdictText = '❌ FAIL — Critical issues require attention';
  } else {
    verdictClass = 'verdict-review';
    verdictText = '⚠️ REVIEW — Minor issues to address';
  }
  verdictBox.innerHTML = `<div class="${verdictClass}"><span class="verdict-text">${verdictText}</span></div>`;

  // Stats
  const statsGrid = document.getElementById('statsGrid');
  statsGrid.innerHTML = `
    <div class="stat-box"><div class="stat-num">${data.doc_qids || 0}</div><div class="stat-label">Questions</div></div>
    <div class="stat-box"><div class="stat-num">${data.live_qids || 0}</div><div class="stat-label">Pages crawled</div></div>
    <div class="stat-box stat-green"><div class="stat-num">${data.term_passed || 0}/${data.term_total || 0}</div><div class="stat-label">Termination pass</div></div>
    <div class="stat-box stat-red"><div class="stat-num">${data.total_issues || 0}</div><div class="stat-label">Issues found</div></div>
  `;

  // Download button
  if (data.report_file) {
    const btn = document.getElementById('downloadBtn');
    btn.href = '/download/' + currentJobId;
    btn.style.display = 'inline-flex';
  }

  // Termination results
  if (data.term_results && data.term_results.length > 0) {
    document.getElementById('termResultCard').style.display = 'block';
    const tbody = document.getElementById('termBody');
    tbody.innerHTML = data.term_results.map(r => `
      <tr>
        <td><span class="badge ${r.passed ? 'badge-pass' : 'badge-fail'}">${r.passed ? '✅ PASS' : '❌ FAIL'}</span></td>
        <td>${r.test_qid}</td>
        <td>${r.answer_code}</td>
        <td style="font-size:11px;color:#666">${r.details}</td>
      </tr>
    `).join('');
  }

  // Issues
  if (data.issues && data.issues.length > 0) {
    document.getElementById('issuesCard').style.display = 'block';
    const tbody = document.getElementById('issuesBody');
    tbody.innerHTML = data.issues.map(i => `
      <tr>
        <td style="font-weight:500">${i.qid}</td>
        <td>${i.type}</td>
        <td><span class="badge badge-${i.severity.toLowerCase()}">${i.severity}</span></td>
        <td style="font-size:11px;color:#555;white-space:pre-wrap;max-width:300px">${i.details.substring(0,200)}</td>
      </tr>
    `).join('');
  }
}

function resetForm() {
  document.getElementById('inputSection').style.display = 'block';
  document.getElementById('heroSection').style.display = 'block';
  document.getElementById('progressSection').classList.remove('visible');
  document.getElementById('resultSection').classList.remove('visible');
  document.getElementById('runBtn').disabled = false;
  document.getElementById('logBox').innerHTML = '';
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('docFile').value = '';
  document.getElementById('ssFiles').value = '';
  document.getElementById('docZone').classList.remove('file-selected');
  document.getElementById('docLabel').textContent = 'Click to upload .docx';
  document.getElementById('ssLabel').textContent = 'Add reference screenshots';
  currentJobId = null;
}
</script>
</body>
</html>'''


# ================================================================
# QC ENGINE (from main.py)
# ================================================================
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
    'me','him','her','us','them','my','your','his','their','our',
    'all','any','some','no','not','so','if','as','than','then',
}


def normalize(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', text).strip().lower()


def fuzzy_match(doc_text, live_text, threshold=0.65):
    if not doc_text or len(doc_text) < 15:
        return True, 1.0
    doc_norm = normalize(doc_text)
    live_norm = normalize(live_text)
    if not live_norm:
        return False, 0.0
    if doc_norm[:40] in live_norm:
        return True, 1.0
    for i in range(0, len(doc_norm)-40, 30):
        if doc_norm[i:i+40] in live_norm:
            return True, 0.9
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


def run_qc_job(job_id, doc_path, survey_url, country, mode, screenshot_paths):
    try:
        from playwright.sync_api import sync_playwright
        from docx import Document
        from docx.oxml.ns import qn
    except ImportError as e:
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['logs'].append({'msg': f'❌ Missing module: {e}', 'color': 'red'})
        return

    job = jobs[job_id]

    def log(msg, color='white'):
        job['logs'].append({'msg': msg, 'color': color})

    def set_progress(p, phase=''):
        job['progress'] = p
        if phase:
            job['phase'] = phase

    try:
        # PHASE 1: Parse document
        set_progress(5, 'Parsing document...')
        log('', 'white')
        log('══════════════════════════════════', 'cyan')
        log('  PHASE 1: DOCUMENT PARSING', 'cyan')
        log('══════════════════════════════════', 'cyan')

        doc = Document(doc_path)
        questions = {}
        qid_pat = re.compile(r'^\s*\[?\s*(?P<qid>[RSQ]\d+(?:bis|ter|Info|info|Ex)?)\s*[\.\-\s\]]')
        current_qid = None

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text or JUNK_RE.search(text):
                continue
            m = qid_pat.match(text)
            if m:
                qid = m.group('qid')
                current_qid = qid
                if qid not in questions:
                    questions[qid] = {
                        "text": "", "options": [], "is_mandatory": False,
                        "has_piping": False, "termination_rules": [], "is_numeric": False
                    }
                rest = text[m.end():].strip()
                rest = re.sub(r'^[\-\u2013\u2014\s]+[A-Z][A-Za-z\s"\'\-\u2013\u2014,]+\]', '', rest).strip()
                if rest and not JUNK_RE.search(rest):
                    questions[qid]["text"] += " " + rest
                continue
            if current_qid:
                opt = re.match(r'^(\d+)[\.\)]\s+(.+)', text)
                if opt:
                    questions[current_qid]["options"].append({
                        "code": opt.group(1), "text": opt.group(2).strip()
                    })
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
        log(f'  ✅ Questions parsed: {len(questions)}', 'green')
        log(f'  ✅ Termination rules: {term_count}', 'green')
        set_progress(15)

        live_data = {}
        issues = []
        term_results = []

        # PHASE 2: Crawl
        if mode in ('full', 'quick'):
            set_progress(20, 'Crawling survey pages...')
            log('', 'white')
            log('══════════════════════════════════', 'cyan')
            log('  PHASE 2: SURVEY CRAWLING', 'cyan')
            log('══════════════════════════════════', 'cyan')

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, slow_mo=200)
                context = browser.new_context(viewport={"width": 1400, "height": 900})
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
                        log(f'  ⚠️  Country select: {str(e)[:50]}', 'yellow')

                # Open navigator
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

                log(f'  📋 {len(qid_index_map)} QIDs found in navigator', 'blue')
                ss_dir = f"{OUTPUT_FOLDER}/{job_id}/screenshots"
                os.makedirs(ss_dir, exist_ok=True)

                total = len(qid_index_map)
                for i, (nav_idx, qid) in enumerate(qid_index_map, 1):
                    progress = 20 + int((i / total) * 40)
                    set_progress(progress, f'Crawling {qid} ({i}/{total})...')
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
                        page.wait_for_timeout(300)

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
                        log(f'   ✅ {qid} ({len(text)} chars)', 'green')
                    except Exception as e:
                        live_data[qid] = {"text":"","options":[],"has_mandatory_marker":False,"has_raw_piping":False,"raw_piping_found":[],"status":f"ERROR: {str(e)[:60]}"}
                        log(f'   ❌ {qid}: {str(e)[:50]}', 'red')

                browser.close()
            log(f'\n  ✅ Crawled {len(live_data)} pages', 'green')

        # PHASE 3: Compare
        if mode in ('full', 'quick') and live_data:
            set_progress(65, 'Comparing doc vs live...')
            log('', 'white')
            log('══════════════════════════════════', 'cyan')
            log('  PHASE 4: COMPARISON', 'cyan')
            log('══════════════════════════════════', 'cyan')

            for qid in sorted(set(questions.keys()) | set(live_data.keys())):
                in_doc = qid in questions
                in_live = qid in live_data
                if in_doc and not in_live:
                    issues.append({"qid":qid,"type":"MISSING IN LIVE","details":"In doc but not in live survey","severity":"HIGH"})
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
                    issues.append({"qid":qid,"type":"TEXT MISMATCH","details":f"Match: {int(ratio*100)}%\nDoc: {doc_text[:100]}\nLive: {live_text[:100]}","severity":"HIGH" if ratio < 0.4 else "MEDIUM"})

                missing = find_missing_words(doc_text, live_text)
                if missing:
                    issues.append({"qid":qid,"type":"WORDS MISSING","details":f"Missing: {missing[:10]}\nDoc: {doc_text[:150]}\nLive: {live_text[:150]}","severity":"HIGH" if len(missing)>=3 else "MEDIUM"})

                doc_opts = [o["text"] for o in questions[qid].get("options",[])]
                live_opts_text = " | ".join([o["text"] for o in live_data[qid].get("options",[])])
                missing_opts = []
                for d_opt in doc_opts:
                    d_norm = normalize(d_opt)
                    if len(d_norm) > 3 and d_norm not in normalize(live_opts_text):
                        found = any(SequenceMatcher(None,d_norm,normalize(lo["text"])).ratio()>0.7 for lo in live_data[qid].get("options",[]))
                        if not found: missing_opts.append(d_opt[:50])
                if missing_opts:
                    issues.append({"qid":qid,"type":"OPTIONS MISMATCH","details":f"Missing: {missing_opts[:5]}","severity":"HIGH"})

                if questions[qid].get("is_mandatory") and not live_data[qid].get("has_mandatory_marker"):
                    issues.append({"qid":qid,"type":"MANDATORY MISSING","details":"Doc mandatory, live marker missing","severity":"MEDIUM"})

                if live_data[qid].get("has_raw_piping"):
                    issues.append({"qid":qid,"type":"PIPING NOT RESOLVED","details":f"Raw: {live_data[qid].get('raw_piping_found',[])[:3]}","severity":"HIGH"})

            sev = {"HIGH":0,"MEDIUM":0,"INFO":0}
            for i in issues: sev[i.get("severity","INFO")] = sev.get(i.get("severity","INFO"),0) + 1
            log(f'  Total issues: {len(issues)} (HIGH:{sev["HIGH"]} MEDIUM:{sev["MEDIUM"]} INFO:{sev["INFO"]})', 'yellow')

        # PHASE 4: Termination testing
        if mode in ('full', 'logic'):
            set_progress(75, 'Testing termination rules...')
            log('', 'white')
            log('══════════════════════════════════', 'cyan')
            log('  PHASE 3: TERMINATION TESTING', 'cyan')
            log('══════════════════════════════════', 'cyan')

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

            log(f'  📋 Testing {len(unique_rules)} rules', 'blue')

            for i, rule in enumerate(unique_rules, 1):
                test_qid = rule["test_qid"]
                answer_code = rule["answer_code"]
                log(f'\n  [{i}/{len(unique_rules)}] {test_qid} = code {answer_code}', 'blue')
                set_progress(75 + int((i/len(unique_rules))*15))

                # Compound check
                raw_upper = rule.get("raw_rule","").upper()
                is_compound = any(w in raw_upper for w in ["NOT SELECTED","AND CODE","OR CODE"]) or test_qid in ["S7","S9"]
                if is_compound:
                    term_results.append({"test_qid":test_qid,"answer_code":answer_code,"passed":True,"details":"MANUAL CHECK — compound logic","source":rule.get("source","")})
                    log(f'      ✅ MANUAL CHECK — compound logic', 'yellow')
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
                            log(f'      ❌ Could not navigate to {test_qid}', 'red')
                            continue

                        try:
                            if page.locator(".sr-tn-question__text").count() > 0:
                                page.locator("text=Test Navigator").first.click(timeout=2000)
                                page.wait_for_timeout(500)
                        except: pass

                        qid_data = questions.get(test_qid, {})
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
                            log(f'      ❌ Click failed', 'red')
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
                            log(f'      ✅ PASS — Terminated', 'green')
                        else:
                            r_result["passed"] = False
                            r_result["details"] = f"Expected close but continued ({strategy})"
                            log(f'      ❌ FAIL — Survey continued', 'red')

                        browser.close()
                except Exception as e:
                    r_result["details"] = f"Error: {str(e)[:80]}"
                    log(f'      ❌ Error: {str(e)[:60]}', 'red')

                term_results.append(r_result)

            passed = sum(1 for r in term_results if r["passed"])
            log(f'\n  📊 Termination: {passed}/{len(term_results)} passed', 'green' if passed==len(term_results) else 'yellow')

        # PHASE 5: Generate report
        set_progress(92, 'Generating report...')
        log('', 'white')
        log('══════════════════════════════════', 'cyan')
        log('  PHASE 5: REPORT GENERATION', 'cyan')
        log('══════════════════════════════════', 'cyan')

        report_path = generate_docx_report(job_id, issues, term_results, questions, live_data)
        log(f'  ✅ Report saved', 'green')

        # Verdict
        sev = {"HIGH":0,"MEDIUM":0,"INFO":0}
        for i in issues: sev[i.get("severity","INFO")] = sev.get(i.get("severity","INFO"),0)+1
        term_failed = sum(1 for r in term_results if not r["passed"])

        if sev['HIGH'] == 0 and term_failed == 0:
            verdict = 'PASS'
        elif sev['HIGH'] > 0 or term_failed > 2:
            verdict = 'FAIL'
        else:
            verdict = 'REVIEW'

        set_progress(100, 'Complete!')
        log('', 'white')
        log('══════════════════════════════════', 'magenta')
        log('  FINAL SUMMARY', 'magenta')
        log('══════════════════════════════════', 'magenta')
        log(f'  Document QIDs:  {len(questions)}', 'blue')
        log(f'  Live QIDs:      {len(live_data)}', 'blue')
        log(f'  Total Issues:   {len(issues)}', 'yellow')
        if term_results:
            p = sum(1 for r in term_results if r["passed"])
            log(f'  Termination:    {p}/{len(term_results)} passed', 'green' if p==len(term_results) else 'yellow')
        log(f'\n  ✅ DONE!', 'green')

        job['status'] = 'done'
        job['verdict'] = verdict
        job['doc_qids'] = len(questions)
        job['live_qids'] = len(live_data)
        job['total_issues'] = len(issues)
        job['term_passed'] = sum(1 for r in term_results if r["passed"])
        job['term_total'] = len(term_results)
        job['term_results'] = term_results
        job['issues'] = issues
        job['report_file'] = report_path

    except Exception as e:
        import traceback
        job['status'] = 'error'
        job['logs'].append({'msg': f'❌ Error: {str(e)}', 'color': 'red'})
        job['logs'].append({'msg': traceback.format_exc(), 'color': 'red'})


def generate_docx_report(job_id, issues, term_results, doc_data, live_data):
    try:
        from docx import Document as DocxDoc
        from docx.shared import Pt, RGBColor, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError:
        return None

    def shade_cell(cell, color_hex):
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:fill'), color_hex)
        tc_pr.append(shd)

    report = DocxDoc()
    for sec in report.sections:
        sec.top_margin = Cm(1.5)
        sec.bottom_margin = Cm(1.5)
        sec.left_margin = Cm(2)
        sec.right_margin = Cm(2)

    title = report.add_heading("Survey QC Report", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    sub = report.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(f"Generated: {datetime.now().strftime('%d %B %Y, %H:%M')}")
    sub_run.font.size = Pt(11)
    sub_run.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

    report.add_paragraph()
    report.add_heading("Summary", level=1)

    sev = {"HIGH":0,"MEDIUM":0,"INFO":0}
    for i in issues: sev[i.get("severity","INFO")] = sev.get(i.get("severity","INFO"),0)+1
    term_passed = sum(1 for r in term_results if r["passed"])
    term_failed = len(term_results) - term_passed

    tbl = report.add_table(rows=2, cols=4)
    tbl.style = 'Light Grid Accent 1'
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (h, v) in enumerate([
        ("Document QIDs", str(len(doc_data))),
        ("Live QIDs", str(len(live_data))),
        ("Total Issues", str(len(issues))),
        ("Termination", f"{term_passed}/{len(term_results)} passed" if term_results else "N/A")
    ]):
        c = tbl.rows[0].cells[i]
        c.text = h
        shade_cell(c, "1F4E79")
        for run in c.paragraphs[0].runs:
            run.font.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            run.font.size = Pt(10)
        c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        c2 = tbl.rows[1].cells[i]
        c2.text = v
        for run in c2.paragraphs[0].runs:
            run.font.bold = True
            run.font.size = Pt(13)
        c2.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    report.add_paragraph()

    if sev['HIGH'] == 0 and term_failed == 0:
        verdict = "✅ PASS — No critical issues found"
        color = (0x00, 0x70, 0x00)
    elif sev['HIGH'] > 0 or term_failed > 2:
        verdict = "❌ FAIL — Critical issues require immediate attention"
        color = (0xC0, 0x00, 0x00)
    else:
        verdict = "⚠️ REVIEW — Minor issues to address"
        color = (0xBF, 0x8F, 0x00)

    p = report.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(verdict)
    run.font.size = Pt(14)
    run.font.bold = True
    run.font.color.rgb = RGBColor(*color)

    report.add_page_break()

    if term_results:
        report.add_heading("Termination Test Results", level=1)
        t = report.add_table(rows=1, cols=4)
        t.style = 'Light Grid Accent 1'
        for i, h in enumerate(["Status", "QID", "Code", "Result"]):
            t.rows[0].cells[i].text = h
            shade_cell(t.rows[0].cells[i], "1F4E79")
            for run in t.rows[0].cells[i].paragraphs[0].runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                run.font.size = Pt(10)
        for r in term_results:
            row = t.add_row()
            row.cells[0].text = "✅ PASS" if r["passed"] else "❌ FAIL"
            row.cells[1].text = r["test_qid"]
            row.cells[2].text = str(r["answer_code"])
            row.cells[3].text = r["details"][:80]
            shade_cell(row.cells[0], "C6EFCE" if r["passed"] else "FFC7CE")
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.font.size = Pt(9)
        report.add_page_break()

    if issues:
        report.add_heading("Issues Found", level=1)
        for sev_level, emoji, col in [("HIGH","🔴","C00000"),("MEDIUM","🟡","BF8F00"),("INFO","🔵","2E75B6")]:
            sev_issues = [i for i in issues if i.get("severity") == sev_level]
            if not sev_issues: continue
            report.add_heading(f"{emoji} {sev_level} ({len(sev_issues)})", level=2)
            t2 = report.add_table(rows=1, cols=3)
            t2.style = 'Light Grid Accent 1'
            for i, h in enumerate(["QID", "Type", "Details"]):
                t2.rows[0].cells[i].text = h
                shade_cell(t2.rows[0].cells[i], "1F4E79")
                for run in t2.rows[0].cells[i].paragraphs[0].runs:
                    run.font.bold = True
                    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                    run.font.size = Pt(10)
            for issue in sev_issues:
                row = t2.add_row()
                row.cells[0].text = issue["qid"]
                row.cells[1].text = issue["type"]
                row.cells[2].text = issue["details"][:250]
                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(9)
            report.add_paragraph()

    os.makedirs(f"{OUTPUT_FOLDER}/{job_id}", exist_ok=True)
    path = f"{OUTPUT_FOLDER}/{job_id}/QC_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.docx"
    report.save(path)
    return path


# ================================================================
# FLASK ROUTES
# ================================================================
@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/run', methods=['POST'])
def run():
    doc_file = request.files.get('doc')
    survey_url = request.form.get('url', '').strip()
    country = request.form.get('country', '').strip()
    mode = request.form.get('mode', 'full')

    if not doc_file or not survey_url:
        return jsonify({'error': 'Doc and URL required'}), 400

    job_id = str(uuid.uuid4())[:8]
    job_dir = f"{UPLOAD_FOLDER}/{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    doc_filename = secure_filename(doc_file.filename)
    doc_path = f"{job_dir}/{doc_filename}"
    doc_file.save(doc_path)

    ss_paths = []
    for f in request.files.getlist('screenshots'):
        if f.filename:
            ss_name = secure_filename(f.filename)
            ss_path = f"{job_dir}/{ss_name}"
            f.save(ss_path)
            ss_paths.append(ss_path)

    jobs[job_id] = {
        'status': 'running',
        'progress': 0,
        'phase': 'Starting...',
        'logs': [],
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
        target=run_qc_job,
        args=(job_id, doc_path, survey_url, country, mode, ss_paths),
        daemon=True
    )
    thread.start()

    return jsonify({'job_id': job_id})


@app.route('/status/<job_id>')
def status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(jobs[job_id])


@app.route('/download/<job_id>')
def download(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    report_file = jobs[job_id].get('report_file')
    if not report_file or not os.path.exists(report_file):
        return jsonify({'error': 'Report not found'}), 404
    return send_file(report_file, as_attachment=True,
                     download_name=f"QC_Report_{job_id}.docx")


@app.route('/feedback', methods=['POST'])
def feedback():
    data = request.json
    feedback_file = f"{OUTPUT_FOLDER}/feedback.json"
    feedbacks = []
    if os.path.exists(feedback_file):
        try:
            with open(feedback_file) as f:
                feedbacks = json.load(f)
        except: pass
    feedbacks.append({
        'timestamp': datetime.now().isoformat(),
        'rating': data.get('rating'),
        'comment': data.get('comment', '')
    })
    with open(feedback_file, 'w') as f:
        json.dump(feedbacks, f, indent=2)
    return jsonify({'ok': True})

if __name__ == '__main__':
    print("\n" + "="*50)
    print("  SurveyQC Web UI v1.0")
    print("="*50)
port = int(os.environ.get('PORT', 5000))
app.run(debug=False, port=port, host='0.0.0.0')
print(f"\n  Open browser: http://localhost:{port}")
    print(f"  Press Ctrl+C to stop\n")
    app.run(debug=False, port=port, host='0.0.0.0')
