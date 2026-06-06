/* ============================================================
   SurveyQC — Shared JS v3.0
   Platform logos, feedback widget, share modal, performance.
   No dependencies. Runs on every page.
   ============================================================ */

(function () {
  'use strict';

  /* ── Helpers ── */
  function qs(sel, ctx) { return (ctx || document).querySelector(sel); }
  function qsa(sel, ctx) { return (ctx || document).querySelectorAll(sel); }
  function on(el, ev, fn) { if (el) el.addEventListener(ev, fn); }
  function ce(tag, attrs, text) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function(k) {
      if (k === 'className') el.className = attrs[k];
      else if (k === 'style') el.style.cssText = attrs[k];
      else el.setAttribute(k, attrs[k]);
    });
    if (text !== undefined) el.textContent = text;
    return el;
  }

  /* ── Mobile hamburger injection ── */
  function initMobileHeader() {
    var sidebar = qs('.sidebar');
    if (!sidebar) return;
    if (qs('.mobile-hdr')) return;

    var hdr = document.createElement('div');
    hdr.className = 'mobile-hdr';
    hdr.id = 'mobile-hdr';
    hdr.innerHTML =
      '<button class="mobile-hdr-btn" id="sidebar-toggle" aria-label="Open menu">' +
        '<i class="ti ti-menu-2"></i>' +
      '</button>' +
      '<span class="mobile-hdr-logo">SurveyQC</span>';
    document.body.insertBefore(hdr, document.body.firstChild);

    var overlay = document.createElement('div');
    overlay.id = 'sidebar-overlay';
    on(overlay, 'click', closeSidebar);
    document.body.appendChild(overlay);

    on(qs('#sidebar-toggle'), 'click', toggleSidebar);
  }

  function toggleSidebar() {
    var sidebar = qs('.sidebar');
    if (!sidebar) return;
    if (sidebar.classList.contains('open')) { closeSidebar(); } else { openSidebar(); }
  }

  function openSidebar() {
    var sidebar = qs('.sidebar');
    var overlay = qs('#sidebar-overlay');
    if (sidebar) sidebar.classList.add('open');
    if (overlay) overlay.classList.add('active');
    document.body.style.overflow = 'hidden';
  }

  function closeSidebar() {
    var sidebar = qs('.sidebar');
    var overlay = qs('#sidebar-overlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('active');
    document.body.style.overflow = '';
  }

  function hookNavLinks() {
    qsa('.sidebar .nav-item').forEach(function (link) {
      on(link, 'click', function () {
        if (window.innerWidth <= 768) closeSidebar();
      });
    });
  }

  /* ── Admin mobile header ── */
  function initAdminMobileHeader() {
    var adminSb = qs('#admsb');
    if (!adminSb) return;
    if (qs('#admin-mobile-hdr')) return;

    var hdr = document.createElement('div');
    hdr.id = 'admin-mobile-hdr';
    hdr.style.cssText = 'display:none;position:fixed;top:0;left:0;right:0;height:54px;background:white;border-bottom:1px solid #E8E1D8;align-items:center;padding:0 14px;z-index:300;gap:12px;box-shadow:0 1px 3px rgba(24,17,10,.06)';
    hdr.innerHTML =
      '<button id="admin-sidebar-toggle" style="background:none;border:none;cursor:pointer;width:44px;height:44px;display:flex;align-items:center;justify-content:center;color:#171717;border-radius:8px" aria-label="Open admin menu">' +
        '<i class="ti ti-menu-2" style="font-size:20px"></i>' +
      '</button>' +
      '<span style="font-size:15px;font-weight:700;color:#171717;letter-spacing:-0.3px;flex:1">Admin</span>';
    document.body.insertBefore(hdr, document.body.firstChild);

    var overlay = document.createElement('div');
    overlay.id = 'admin-sidebar-overlay';
    overlay.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:299;backdrop-filter:blur(1px)';
    on(overlay, 'click', closeAdminSidebar);
    document.body.appendChild(overlay);

    on(qs('#admin-sidebar-toggle'), 'click', toggleAdminSidebar);

    applyAdminMobileLayout();
    on(window, 'resize', applyAdminMobileLayout);
  }

  function applyAdminMobileLayout() {
    var hdr = qs('#admin-mobile-hdr');
    var adminSb = qs('#admsb');
    if (!hdr) return;

    if (window.innerWidth <= 768) {
      hdr.style.display = 'flex';
      if (adminSb) {
        adminSb.style.transform = 'translateX(-100%)';
        adminSb.style.transition = 'transform .2s cubic-bezier(.4,0,.2,1)';
        adminSb.style.zIndex = '300';
      }
      var content = qs('body > div:not(#admin-mobile-hdr):not(#admin-sidebar-overlay):not(#admsb)');
      if (content) content.style.paddingTop = '70px';
    } else {
      hdr.style.display = 'none';
      if (adminSb) {
        adminSb.style.transform = '';
        adminSb.style.zIndex = '9999';
      }
      var content2 = qs('body > div:not(#admin-mobile-hdr):not(#admin-sidebar-overlay):not(#admsb)');
      if (content2) content2.style.paddingTop = '';
    }
  }

  function toggleAdminSidebar() {
    var sb = qs('#admsb');
    if (!sb) return;
    if (sb.classList.contains('open')) { closeAdminSidebar(); } else { openAdminSidebar(); }
  }

  function openAdminSidebar() {
    var sb = qs('#admsb');
    var overlay = qs('#admin-sidebar-overlay');
    if (sb) { sb.classList.add('open'); sb.style.transform = 'translateX(0)'; }
    if (overlay) overlay.style.display = 'block';
    document.body.style.overflow = 'hidden';
  }

  function closeAdminSidebar() {
    var sb = qs('#admsb');
    var overlay = qs('#admin-sidebar-overlay');
    if (sb) { sb.classList.remove('open'); sb.style.transform = 'translateX(-100%)'; }
    if (overlay) overlay.style.display = 'none';
    document.body.style.overflow = '';
  }

  /* ── Admin stat cards: clickable ── */
  function initAdminCardLinks() {
    var path = window.location.pathname;
    if (path !== '/admin' && !path.startsWith('/admin/')) return;

    var map = {
      'total users': '/admin/users',
      'paid users':  '/admin/users',
      'total reports': '/admin/reports',
    };

    qsa('.stat-card').forEach(function (card) {
      var labelEl = card.querySelector('.stat-label');
      if (!labelEl) return;
      var key = labelEl.textContent.trim().toLowerCase();
      var href = map[key];
      if (!href) return;
      card.classList.add('clickable-card');
      card.style.cursor = 'pointer';
      card.setAttribute('role', 'link');
      card.setAttribute('aria-label', labelEl.textContent.trim());
      card.setAttribute('tabindex', '0');
      on(card, 'click', function () { window.location.href = href; });
      on(card, 'keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); window.location.href = href; }
      });
    });
  }

  /* ── Quick-action hover feedback ── */
  function initQuickActions() {
    qsa('[style*="background:rgba(255,255,255,.04)"]').forEach(function (el) {
      el.style.transition = 'all .15s cubic-bezier(.4,0,.2,1)';
      el.style.borderRadius = '10px';
      on(el, 'mouseenter', function () {
        el.style.background = 'rgba(255,255,255,.09)';
        el.style.transform = 'translateY(-1px)';
      });
      on(el, 'mouseleave', function () {
        el.style.background = 'rgba(255,255,255,.04)';
        el.style.transform = '';
      });
    });
  }

  /* ── Mobile bottom nav active state ── */
  function initBottomNav() {
    var path = window.location.pathname;
    qsa('.mobile-bottom-nav a, .mobile-nav-item').forEach(function (a) {
      var href = a.getAttribute('href');
      if (href && path.startsWith(href) && href !== '/') {
        a.style.color = 'var(--accent)';
      }
    });
  }

  /* ── Table: horizontal scroll on mobile ── */
  function initTableScroll() {
    qsa('.card table, .card .data-table').forEach(function (tbl) {
      var card = tbl.closest('.card');
      if (!card) return;
      card.style.overflowX = 'auto';
      card.style.webkitOverflowScrolling = 'touch';
    });
  }

  /* ================================================================
     PLATFORM LOGOS (Task 5)
     Finds any element with data-platform attribute and injects
     a branded icon badge next to it.
  ================================================================ */
  var PLATFORM_CONFIG = {
    confirmit:    { label: 'Confirmit',     color: '#00A651', icon: 'C' },
    forsta:       { label: 'Forsta',        color: '#6B3FA0', icon: 'F' },
    decipher:     { label: 'Decipher',      color: '#1A5276', icon: 'D' },
    qualtrics:    { label: 'Qualtrics',     color: '#0073C6', icon: 'Q' },
    surveymonkey: { label: 'SurveyMonkey',  color: '#00BF6F', icon: 'SM' },
    alchemer:     { label: 'Alchemer',      color: '#00A3B4', icon: 'A' },
    voxco:        { label: 'Voxco',         color: '#E05C00', icon: 'V' },
    other:        { label: 'Other',         color: '#8A847A', icon: '?' },
  };

  function renderPlatformBadges() {
    qsa('[data-platform]').forEach(function (el) {
      if (el.querySelector('.platform-badge')) return; // already rendered
      var raw = (el.getAttribute('data-platform') || '').toLowerCase().replace(/\s+/g, '');
      var cfg = PLATFORM_CONFIG[raw] || PLATFORM_CONFIG['other'];

      var badge = document.createElement('span');
      badge.className = 'platform-badge';
      badge.title = cfg.label;
      badge.style.cssText =
        'display:inline-flex;align-items:center;justify-content:center;' +
        'width:22px;height:22px;border-radius:6px;font-size:9px;font-weight:800;' +
        'color:#fff;background:' + cfg.color + ';margin-right:6px;' +
        'vertical-align:middle;flex-shrink:0;letter-spacing:-0.3px;';
      badge.textContent = cfg.icon;

      el.insertBefore(badge, el.firstChild);
    });
  }

  /* ================================================================
     TOAST NOTIFICATIONS (shared helper)
  ================================================================ */
  function showToast(msg, type) {
    var toast = qs('#toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'toast';
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = 'toast-show' + (type === 'error' ? ' toast-error' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { toast.className = ''; }, 3200);
  }

  /* ================================================================
     SHARE MODAL (Task 4)
     Opens when user clicks a share button with data-share-url
     and data-share-title attributes.
  ================================================================ */
  function initShareModal() {
    var overlay = qs('#share-modal-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'share-modal-overlay';
      overlay.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:1100;backdrop-filter:blur(2px);align-items:center;justify-content:center;';
      overlay.innerHTML =
        '<div id="share-modal" style="background:#fff;border-radius:16px;padding:24px;max-width:420px;width:calc(100% - 32px);box-shadow:0 20px 60px rgba(0,0,0,.18);">' +
          '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;">' +
            '<h3 style="font-size:16px;font-weight:700;margin:0;">Share Report</h3>' +
            '<button id="share-modal-close" style="background:none;border:none;cursor:pointer;font-size:20px;color:#8A847A;width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:8px;">&#x2715;</button>' +
          '</div>' +
          '<div style="background:#F7F4EE;border-radius:10px;padding:10px 12px;display:flex;align-items:center;gap:8px;margin-bottom:16px;">' +
            '<input id="share-url-input" readonly style="flex:1;border:none;background:none;font-size:13px;color:#171717;outline:none;font-family:inherit;" />' +
            '<button id="share-copy-btn" style="background:var(--accent);color:#fff;border:none;border-radius:8px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;">Copy</button>' +
          '</div>' +
          '<div class="share-via" style="display:flex;gap:8px;flex-wrap:wrap;">' +
            '<button class="share-via-btn" id="share-whatsapp" style="display:flex;align-items:center;gap:6px;padding:9px 14px;border-radius:10px;font-size:12px;font-weight:600;border:1.5px solid var(--border);background:#fff;cursor:pointer;flex:1;justify-content:center;min-height:44px;font-family:inherit;">' +
              '<svg width="16" height="16" viewBox="0 0 24 24" fill="#25D366"><path d="M12 2C6.477 2 2 6.477 2 12c0 1.89.525 3.66 1.438 5.168L2 22l4.832-1.438A9.96 9.96 0 0 0 12 22c5.523 0 10-4.477 10-10S17.523 2 12 2zm5.007 13.993c-.214.6-1.247 1.147-1.714 1.214-.466.067-1.014.093-1.633-.1-.376-.12-.86-.28-1.48-.547-2.6-1.12-4.3-3.74-4.427-3.913-.127-.174-1.04-1.38-1.04-2.633 0-1.254.653-1.874.887-2.127.233-.253.507-.313.673-.313.167 0 .333.002.48.009.153.007.36-.059.56.427.2.486.68 1.674.74 1.794.06.12.1.26.02.414-.08.153-.12.247-.24.38-.12.133-.253.3-.36.4-.12.12-.247.247-.107.487.14.24.62 1.02 1.334 1.654.914.807 1.687 1.054 1.927 1.174.24.12.38.1.52-.06.14-.16.6-.7.76-.94.16-.24.32-.2.54-.12.22.08 1.394.66 1.634.78.24.12.4.18.46.28.06.1.06.567-.154 1.167z"/></svg>' +
              'WhatsApp' +
            '</button>' +
            '<button class="share-via-btn" id="share-email" style="display:flex;align-items:center;gap:6px;padding:9px 14px;border-radius:10px;font-size:12px;font-weight:600;border:1.5px solid var(--border);background:#fff;cursor:pointer;flex:1;justify-content:center;min-height:44px;font-family:inherit;">' +
              '<i class="ti ti-mail" style="font-size:15px;"></i>Email' +
            '</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);

      on(qs('#share-modal-close'), 'click', closeShareModal);
      on(overlay, 'click', function(e) { if (e.target === overlay) closeShareModal(); });
      on(qs('#share-copy-btn'), 'click', function() {
        var input = qs('#share-url-input');
        if (!input) return;
        navigator.clipboard ? navigator.clipboard.writeText(input.value).then(function(){
          showToast('Link copied!');
        }) : (input.select(), document.execCommand('copy'), showToast('Link copied!'));
      });
      on(qs('#share-whatsapp'), 'click', function() {
        var url = qs('#share-url-input').value;
        var title = overlay._shareTitle || 'QC Report';
        window.open('https://wa.me/?text=' + encodeURIComponent(title + '\n' + url), '_blank');
      });
      on(qs('#share-email'), 'click', function() {
        var url = qs('#share-url-input').value;
        var title = overlay._shareTitle || 'QC Report';
        window.location.href = 'mailto:?subject=' + encodeURIComponent(title) + '&body=' + encodeURIComponent('Here is the QC report:\n\n' + url);
      });
    }

    // Wire up share trigger buttons
    on(document, 'click', function(e) {
      var btn = e.target.closest('[data-share-url]');
      if (!btn) return;
      e.preventDefault();
      openShareModal(btn.getAttribute('data-share-url'), btn.getAttribute('data-share-title') || 'QC Report');
    });
  }

  function openShareModal(url, title) {
    var overlay = qs('#share-modal-overlay');
    if (!overlay) return;
    overlay._shareTitle = title;
    var input = qs('#share-url-input');
    if (input) input.value = url;
    overlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
  }

  function closeShareModal() {
    var overlay = qs('#share-modal-overlay');
    if (overlay) overlay.style.display = 'none';
    document.body.style.overflow = '';
  }

  /* Expose globally for inline onclick */
  window.openShareModal = openShareModal;
  window.closeShareModal = closeShareModal;

  /* ================================================================
     FEEDBACK WIDGET (Task 6)
     Floating action button + slide-up modal. Submits to
     POST /api/user-feedback.
  ================================================================ */
  function initFeedbackWidget() {
    // Don't inject on admin pages
    if (window.location.pathname.startsWith('/admin')) return;
    if (qs('#feedback-fab')) return;

    var fab = document.createElement('button');
    fab.id = 'feedback-fab';
    fab.setAttribute('aria-label', 'Send feedback');
    fab.innerHTML = '<i class="ti ti-message-circle-heart"></i><span>Feedback</span>';
    document.body.appendChild(fab);

    var overlay = document.createElement('div');
    overlay.id = 'feedback-modal-overlay';
    overlay.innerHTML =
      '<div id="feedback-modal">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">' +
          '<h3 style="font-size:15px;font-weight:700;margin:0;display:flex;align-items:center;gap:8px;">' +
            '<i class="ti ti-message-circle-heart" style="color:var(--accent);"></i>Share Feedback' +
          '</h3>' +
          '<button id="feedback-close-btn" style="background:none;border:none;cursor:pointer;font-size:18px;color:#8A847A;width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:8px;">&#x2715;</button>' +
        '</div>' +
        '<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;" id="feedback-type-row">' +
          '<button class="type-chip active" data-type="general">General</button>' +
          '<button class="type-chip" data-type="bug">Bug</button>' +
          '<button class="type-chip" data-type="feature">Feature</button>' +
          '<button class="type-chip" data-type="other">Other</button>' +
        '</div>' +
        '<textarea id="feedback-msg" placeholder="Tell us what you think, what broke, or what would make this tool better for you..." style="width:100%;min-height:110px;border:1.5px solid var(--border);border-radius:10px;padding:10px 12px;font-size:13px;font-family:inherit;resize:vertical;outline:none;box-sizing:border-box;color:var(--text);background:#fff;transition:border-color .2s;"></textarea>' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:12px;gap:10px;">' +
          '<span id="feedback-char" style="font-size:11px;color:#8A847A;">0 / 1000</span>' +
          '<div style="display:flex;gap:8px;">' +
            '<button id="feedback-cancel-btn" style="padding:8px 14px;border-radius:8px;border:1.5px solid var(--border);background:#fff;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;color:var(--text);">Cancel</button>' +
            '<button id="feedback-submit-btn" style="padding:8px 18px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;">Send</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    // Type chip toggle
    var selectedType = 'general';
    qsa('.type-chip', overlay).forEach(function(chip) {
      on(chip, 'click', function() {
        qsa('.type-chip', overlay).forEach(function(c) { c.classList.remove('active'); });
        chip.classList.add('active');
        selectedType = chip.getAttribute('data-type');
      });
    });

    // Character counter
    var textarea = qs('#feedback-msg');
    var charEl = qs('#feedback-char');
    on(textarea, 'input', function() {
      var len = textarea.value.length;
      if (len > 1000) textarea.value = textarea.value.slice(0, 1000);
      charEl.textContent = Math.min(len, 1000) + ' / 1000';
    });
    on(textarea, 'focus', function() { textarea.style.borderColor = 'var(--accent)'; });
    on(textarea, 'blur', function() { textarea.style.borderColor = 'var(--border)'; });

    // Open / close
    on(fab, 'click', openFeedbackModal);
    on(qs('#feedback-close-btn'), 'click', closeFeedbackModal);
    on(qs('#feedback-cancel-btn'), 'click', closeFeedbackModal);
    on(overlay, 'click', function(e) { if (e.target === overlay) closeFeedbackModal(); });

    // Submit
    on(qs('#feedback-submit-btn'), 'click', function() {
      var msg = (textarea.value || '').trim();
      if (!msg) { showToast('Please write a message first.', 'error'); return; }
      var btn = qs('#feedback-submit-btn');
      btn.disabled = true;
      btn.textContent = 'Sending...';
      fetch('/api/user-feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: selectedType, message: msg, page: window.location.pathname })
      }).then(function(r) {
        if (r.ok) {
          closeFeedbackModal();
          showToast('Thank you for your feedback!');
          textarea.value = '';
          charEl.textContent = '0 / 1000';
        } else {
          showToast('Could not send — please try again.', 'error');
        }
      }).catch(function() {
        showToast('Network error — please try again.', 'error');
      }).finally(function() {
        btn.disabled = false;
        btn.textContent = 'Send';
      });
    });
  }

  function openFeedbackModal() {
    var overlay = qs('#feedback-modal-overlay');
    if (overlay) overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  function closeFeedbackModal() {
    var overlay = qs('#feedback-modal-overlay');
    if (overlay) overlay.classList.remove('open');
    document.body.style.overflow = '';
  }

  /* ================================================================
     PERFORMANCE (Task 8)
     – Lazy-fade images and heavy sections
     – Page transition on internal navigation
     – Intersection Observer for badge/stat count-up
  ================================================================ */
  function initLazyFade() {
    if (!('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('loaded');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.1 });
    qsa('.lazy-fade').forEach(function(el) { io.observe(el); });
  }

  function initPageTransitions() {
    var main = qs('.main-content');
    if (!main) return;
    main.classList.add('page-fade-in');

    on(document, 'click', function(e) {
      var a = e.target.closest('a');
      if (!a) return;
      var href = a.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('mailto:') ||
          href.startsWith('http') || a.getAttribute('target') === '_blank' ||
          a.hasAttribute('download') || e.ctrlKey || e.metaKey || e.shiftKey) return;
      e.preventDefault();
      main.style.opacity = '0';
      main.style.transform = 'translateY(4px)';
      main.style.transition = 'opacity .15s ease, transform .15s ease';
      setTimeout(function() { window.location.href = href; }, 150);
    });
  }

  /* ================================================================
     INIT
  ================================================================ */
  function init() {
    initMobileHeader();
    hookNavLinks();
    initAdminMobileHeader();
    initAdminCardLinks();
    initQuickActions();
    initBottomNav();
    initTableScroll();
    renderPlatformBadges();
    initShareModal();
    initFeedbackWidget();
    initLazyFade();
    initPageTransitions();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* Expose globals */
  window.toggleSidebar   = toggleSidebar;
  window.closeSidebar    = closeSidebar;
  window.showToast       = showToast;
  window.openFeedbackModal  = openFeedbackModal;
  window.closeFeedbackModal = closeFeedbackModal;
  window.renderPlatformBadges = renderPlatformBadges;

})();


/* ═══════════════════════════════════════════════════════════════════
   Drag-Drop Upload Zones — /new-qc page
   Overrides inline template versions, which are broken by Python
   f-string processing converting \' → ' in the onclick construction.
   Runs last because app.js loads with <script defer>.
═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  function _dzOk(file, accept) {
    if (!accept) return true;
    var ext = '.' + file.name.split('.').pop().toLowerCase();
    return accept.toLowerCase().split(',')
      .map(function (s) { return s.trim(); })
      .some(function (t) { return t === ext; });
  }

  function dzOver(e, el) {
    e.preventDefault();
    el.classList.add('dz-over');
  }

  function dzLeave(el) {
    el.classList.remove('dz-over');
  }

  function dzDrop(e, el, inpId, doneId, multi) {
    e.preventDefault();
    el.classList.remove('dz-over');
    var inp = document.getElementById(inpId);
    if (!inp || !e.dataTransfer || !e.dataTransfer.files.length) return;
    try {
      var dt = new DataTransfer();
      for (var i = 0; i < e.dataTransfer.files.length; i++) dt.items.add(e.dataTransfer.files[i]);
      inp.files = dt.files;
    } catch (ex) { return; }
    dzPick(inp, el.id, doneId, multi);
  }

  function dzPick(inp, zoneId, doneId, multi) {
    if (!inp || !inp.files || !inp.files[0]) return;
    var zone = document.getElementById(zoneId);
    if (!zone) return;
    var done = doneId ? document.getElementById(doneId) : null;

    if (!zone.dataset.dzOrig) zone.dataset.dzOrig = zone.innerHTML;

    var files = inp.files, f = files[0];

    if (inp.accept && !_dzOk(f, inp.accept)) {
      zone.classList.remove('dz-ok', 'dz-over');
      zone.classList.add('dz-err');
      zone.innerHTML =
        '<i class="ti ti-file-x" style="font-size:22px;color:#DC2626"></i>' +
        '<p style="font-size:12px;font-weight:600;color:#DC2626;margin:5px 0 2px">Wrong file type</p>' +
        '<p style="font-size:10px;color:#EF4444">Accepted: ' + inp.accept + '</p>';
      try { inp.value = ''; inp.files = new DataTransfer().files; } catch (ex) {}
      if (done) done.style.display = 'none';
      setTimeout(function () {
        if (zone.classList.contains('dz-err')) {
          zone.classList.remove('dz-err');
          if (zone.dataset.dzOrig) { zone.innerHTML = zone.dataset.dzOrig; delete zone.dataset.dzOrig; }
        }
      }, 2500);
      return;
    }

    var sz = f.size < 1048576
      ? (Math.round(f.size / 1024) + ' KB')
      : (Math.round(f.size / 1048576 * 10) / 10 + ' MB');
    var nameLabel = (multi && files.length > 1) ? (files.length + ' files selected') : f.name;
    var sizeLabel = (multi && files.length > 1) ? 'Ready' : '(' + sz + ')';

    zone.classList.remove('dz-err', 'dz-over');
    zone.classList.add('dz-ok');

    // Use DOM methods — avoids the f-string \' → ' quote-escaping bug
    // that breaks string-concatenated onclick attributes in the inline version
    var wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;align-items:center;gap:9px;width:100%;padding:0 2px';

    var checkIcon = document.createElement('i');
    checkIcon.className = 'ti ti-circle-check';
    checkIcon.style.cssText = 'font-size:24px;color:#16A34A;flex-shrink:0';

    var info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:0;text-align:left;overflow:hidden';

    var nameLine = document.createElement('p');
    nameLine.style.cssText = 'font-size:12px;font-weight:600;color:#1A1A2E;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;width:100%;max-width:100%;display:block';
    nameLine.textContent = nameLabel;

    var sizeLine = document.createElement('p');
    sizeLine.style.cssText = 'font-size:10px;color:#6B7280;margin:2px 0 0';
    sizeLine.textContent = sizeLabel;

    info.appendChild(nameLine);
    info.appendChild(sizeLine);

    var rmBtn = document.createElement('button');
    rmBtn.type = 'button';
    rmBtn.title = 'Remove';
    rmBtn.innerHTML = '&#215;';
    rmBtn.style.cssText = 'background:none;border:none;cursor:pointer;color:#9CA3AF;font-size:18px;padding:0 2px;flex-shrink:0;line-height:1;transition:color .15s';
    rmBtn._dzInpId  = inp.id;
    rmBtn._dzZoneId = zoneId;
    rmBtn._dzDoneId = doneId || '';
    rmBtn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      dzClear(this._dzInpId, this._dzZoneId, this._dzDoneId);
    });
    rmBtn.addEventListener('mouseenter', function () { this.style.color = '#DC2626'; });
    rmBtn.addEventListener('mouseleave', function () { this.style.color = '#9CA3AF'; });

    wrapper.appendChild(checkIcon);
    wrapper.appendChild(info);
    wrapper.appendChild(rmBtn);

    zone.innerHTML = '';
    zone.appendChild(wrapper);

    if (done) done.style.display = 'none';
    if (typeof window.updateMeter === 'function') window.updateMeter();
  }

  function dzClear(inpId, zoneId, doneId) {
    var inp  = inpId  ? document.getElementById(inpId)  : null;
    var zone = zoneId ? document.getElementById(zoneId) : null;
    var done = doneId ? document.getElementById(doneId) : null;
    if (inp) { try { inp.value = ''; inp.files = new DataTransfer().files; } catch (e) {} }
    if (done) done.style.display = 'none';
    if (zone) {
      zone.classList.remove('dz-ok', 'dz-err');
      if (zone.dataset.dzOrig) { zone.innerHTML = zone.dataset.dzOrig; delete zone.dataset.dzOrig; }
    }
    if (typeof window.updateMeter === 'function') window.updateMeter();
  }

  window.dzOver  = dzOver;
  window.dzLeave = dzLeave;
  window.dzDrop  = dzDrop;
  window.dzPick  = dzPick;
  window.dzClear = dzClear;

})();
