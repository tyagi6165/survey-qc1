/* new_qc.js — all JS for the New QC page */

/* ── drag-drop ──────────────────────────────────────────────────────── */
function dzOver(e,el){e.preventDefault();el.classList.add('dz-over')}
function dzLeave(el){el.classList.remove('dz-over')}

function _dzOk(file,accept){
  if(!accept) return true;
  var ext='.'+file.name.split('.').pop().toLowerCase();
  return accept.toLowerCase().split(',').map(function(s){return s.trim();})
               .some(function(t){return t===ext;});
}

function dzDrop(e,el,inpId,doneId,multi){
  e.preventDefault();el.classList.remove('dz-over');
  var inp=document.getElementById(inpId);
  if(e.dataTransfer&&e.dataTransfer.files.length){
    var dt=new DataTransfer();
    for(var i=0;i<e.dataTransfer.files.length;i++) dt.items.add(e.dataTransfer.files[i]);
    inp.files=dt.files;
    dzPick(inp,el.id,doneId,multi);
  }
}

function dzPick(inp,zoneId,doneId,multi){
  if(!inp.files||!inp.files[0]) return;
  var zone=document.getElementById(zoneId); if(!zone) return;
  var done=doneId?document.getElementById(doneId):null;
  if(!zone.dataset.dzOrig) zone.dataset.dzOrig=zone.innerHTML;
  var files=inp.files,f=files[0];
  if(inp.accept&&!_dzOk(f,inp.accept)){
    zone.classList.remove('dz-ok','dz-over'); zone.classList.add('dz-err');
    zone.innerHTML='<i class="ti ti-file-x" style="font-size:22px;color:#DC2626"></i>'
      +'<p style="font-size:12px;font-weight:600;color:#DC2626;margin:5px 0 2px">Wrong file type</p>'
      +'<p style="font-size:10px;color:#EF4444">Accepted: '+inp.accept+'</p>';
    try{inp.value='';inp.files=new DataTransfer().files;}catch(ex){}
    if(done) done.style.display='none';
    var _z=zone;
    setTimeout(function(){
      if(_z.classList.contains('dz-err')){
        _z.classList.remove('dz-err');
        if(_z.dataset.dzOrig){_z.innerHTML=_z.dataset.dzOrig;delete _z.dataset.dzOrig;}
      }
    },2500);
    return;
  }
  var sz=f.size<1048576?(Math.round(f.size/1024)+' KB'):(Math.round(f.size/1048576*10)/10+' MB');
  var nameLabel=(multi&&files.length>1)?(files.length+' files selected'):f.name;
  var sizeLabel=(multi&&files.length>1)?'Ready':'('+sz+')';
  zone.classList.remove('dz-err','dz-over'); zone.classList.add('dz-ok');
  zone.innerHTML=
    '<div style="display:flex;align-items:center;gap:9px;width:100%;padding:0 2px">'
   +'<i class="ti ti-circle-check" style="font-size:24px;color:#16A34A;flex-shrink:0"></i>'
   +'<div style="flex:1;min-width:0;text-align:left">'
   +'<p style="font-size:12px;font-weight:600;color:#1A1A2E;margin:0;white-space:nowrap;'
   +'overflow:hidden;text-overflow:ellipsis">'+nameLabel+'</p>'
   +'<p style="font-size:10px;color:#6B7280;margin:2px 0 0">'+sizeLabel+'</p>'
   +'</div>'
   +'<button type="button" style="background:none;border:none;cursor:pointer;color:#9CA3AF;'
   +'font-size:18px;padding:0 2px;flex-shrink:0;line-height:1"'
   +' onclick="event.stopPropagation();dzClear(\''+inp.id+'\',\''+zoneId+'\',\''+(doneId||'')+'\');updateMeter()"'
   +' title="Remove">&#215;</button>'
   +'</div>';
  if(done) done.style.display='none';
  updateMeter();
}

function dzClear(inpId,zoneId,doneId){
  var inp=inpId?document.getElementById(inpId):null;
  var zone=zoneId?document.getElementById(zoneId):null;
  var done=doneId?document.getElementById(doneId):null;
  if(inp){try{inp.value='';inp.files=new DataTransfer().files;}catch(e){}}
  if(done) done.style.display='none';
  if(zone){
    zone.classList.remove('dz-ok','dz-err');
    if(zone.dataset.dzOrig){zone.innerHTML=zone.dataset.dzOrig;delete zone.dataset.dzOrig;}
  }
  updateMeter();
}

/* ── accuracy meter ─────────────────────────────────────────────────── */
function updateMeter(){
  var docZone=document.getElementById('docZone');
  var xmlZone=document.getElementById('xmlZone');
  var docDone=!!(docZone&&docZone.classList.contains('dz-ok'));
  var xmlDone=!!(xmlZone&&xmlZone.classList.contains('dz-ok'));
  var urlVal=((document.getElementById('urlInput')||{}).value||'').trim();
  var fill=document.getElementById('accFill');
  var lbl=document.getElementById('accLbl');
  var btn=document.getElementById('runBtn');
  var sub=document.getElementById('runBtnSub');
  var txt=document.getElementById('runBtnText');
  if(!fill||!lbl||!btn) return;
  var hasUrl=urlVal.length>10;

  if(docDone&&xmlDone&&hasUrl){
    /* ADVANCED QC — all three inputs provided */
    fill.style.width='100%'; fill.style.background='#16A34A';
    lbl.textContent='ADVANCED QC — DOC + XML + LIVE verification'; lbl.style.color='#16A34A';
    txt.textContent='► Run Advanced QC — 3-5 minutes';
    if(sub) sub.textContent="Full verification: doc vs XML vs live survey";
    btn.className='run-btn btn-full';
    btn.disabled=false;
    btn.style.background='';
    btn.style.opacity='1';
    btn.style.cursor='pointer';
  } else if(docDone&&xmlDone){
    /* STANDARD QC — doc + xml, no live URL */
    fill.style.width='75%'; fill.style.background='#3B82F6';
    lbl.textContent='STANDARD QC — DOC + XML (add URL for live verification)'; lbl.style.color='#2563EB';
    txt.textContent='► Run Standard QC — 1-2 minutes';
    if(sub) sub.textContent="XML-based QC — add Survey URL for advanced live checks";
    btn.className='run-btn';
    btn.disabled=false;
    btn.style.background='#2563EB';
    btn.style.opacity='1';
    btn.style.cursor='pointer';
  } else {
    var n=(docDone?1:0)+(xmlDone?1:0);
    var pct=Math.round(n/2*50);
    fill.style.width=pct+'%'; fill.style.background='#D1D5DB';
    if(!docDone) lbl.textContent='Upload Spec Document to continue';
    else lbl.textContent='Upload Survey Export (XML/QSF/ZIP) to continue';
    lbl.style.color='#9CA3AF';
    txt.textContent='► Run QC — Upload files above';
    if(sub) sub.textContent='Upload Spec Document + Survey Export to run';
    btn.className='run-btn'; btn.disabled=true; btn.style.background=''; btn.style.opacity='';
  }
}

/* ── platform detection ─────────────────────────────────────────────── */
var platMap=[[/confirmit/i,'Confirmit'],[/decipher/i,'Decipher'],
  [/forsta/i,'Forsta'],[/qualtrics/i,'Qualtrics'],[/surveymonkey/i,'SurveyMonkey']];
function detectPlat(v){
  var b=document.getElementById('platBadge'); if(!b) return;
  var found='';
  for(var i=0;i<platMap.length;i++){if(platMap[i][0].test(v)){found=platMap[i][1];break;}}
  if(found){b.textContent='✓ '+found;b.style.display='inline-block';}
  else b.style.display='none';
}
function setUrlHint(p){
  var h={confirmit:'https://survey.confirmit.com/wix/',
         decipher:'https://survey.decipherinc.com/',
         forsta:'https://survey.forsta.com/',
         qualtrics:'https://survey.qualtrics.com/',
         surveymonkey:'https://www.surveymonkey.com/r/'};
  var inp=document.getElementById('urlInput');
  if(inp&&!inp.value&&h[p]) inp.placeholder='e.g. '+h[p]+'...';
  detectPlat(p);
}

/* ── toggles ────────────────────────────────────────────────────────── */
function toggleXmlTip(){
  var t=document.getElementById('xmlTipBox');
  if(t) t.style.display=t.style.display==='none'?'block':'none';
}
function toggleAdv(){
  var p=document.getElementById('advPanel');
  var c=document.getElementById('advChev');
  if(!p) return;
  var open=p.style.display==='none';
  p.style.display=open?'block':'none';
  if(c) c.innerHTML=open?'&#9652;':'&#9662;';
}
function onExportFile(inp){
  if(!inp.files||!inp.files[0]) return;
  var f=inp.files[0];
  if(f.name.split('.').pop().toLowerCase()==='xlsx') return;
  var reader=new FileReader();
  reader.onload=function(e){
    var firstLine=((e.target.result||'').trim()).split(/\r?\n/)[0];
    var ta=document.getElementById('exportHeadersText');
    if(ta&&firstLine) ta.value=firstLine;
  };
  reader.readAsText(f);
}

/* ── submit / upgrade-modal intercept ───────────────────────────────── */
document.addEventListener('DOMContentLoaded', function(){
  var form=document.getElementById('qcForm');
  if(form){
    form.addEventListener('submit',function(e){
      if(form.dataset.atLimit==='1'){
        e.preventDefault();
        var m=document.getElementById('upgradeModal');
        if(m) m.style.display='flex';
        return;
      }
      var btn=document.getElementById('runBtn');
      var txt=document.getElementById('runBtnText');
      if(btn){btn.disabled=true;btn.style.opacity='.6';}
      if(txt) txt.textContent='Processing…';
    });
  }

  var _url=document.getElementById('urlInput');
  if(_url) _url.addEventListener('input', updateMeter);
  updateMeter();

  /* debug */
  var _dz=document.getElementById('docZone');
  var _xz=document.getElementById('xmlZone');
  var _ui=document.getElementById('urlInput');
  var _rb=document.getElementById('runBtn');
  console.log('docZone has dz-ok:', !!(_dz&&_dz.classList.contains('dz-ok')));
  console.log('xmlZone has dz-ok:', !!(_xz&&_xz.classList.contains('dz-ok')));
  console.log('URL value:', _ui?_ui.value.length:0);
  console.log('Button disabled:', _rb?_rb.disabled:null);
});
