/* Fantamorte — frontend helpers (PWA, push, theme, toast) */
(function () {
  'use strict';

  // -------- Theme (auto | light | dark) --------
  const html = document.documentElement;
  const THEME_PREFS = ['auto', 'light', 'dark'];
  const THEME_LABELS = { auto: 'automatico', light: 'chiaro', dark: 'scuro' };
  const THEME_ICONS = { auto: '◐', light: '☀', dark: '☾' };
  const mql = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function readPref() {
    const stored = localStorage.getItem('fm-theme');
    if (THEME_PREFS.indexOf(stored) !== -1) return stored;
    const fromProfile = html.dataset.profileTheme;
    return THEME_PREFS.indexOf(fromProfile) !== -1 ? fromProfile : 'auto';
  }
  function effectiveTheme(pref) {
    if (pref === 'auto') return (mql && mql.matches) ? 'dark' : 'light';
    return pref;
  }
  function applyPref(pref, persist) {
    const effective = effectiveTheme(pref);
    html.setAttribute('data-fm-theme', effective);
    html.setAttribute('data-theme-pref', pref);
    if (persist) localStorage.setItem('fm-theme', pref);
    const btn = document.getElementById('fmThemeBtn');
    if (btn) {
      btn.textContent = THEME_ICONS[pref];
      const label = 'Tema: ' + THEME_LABELS[pref] + ' (clicca per cambiare)';
      btn.title = label;
      btn.setAttribute('aria-label', label);
    }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      // Il colore per tema vive nei token CSS (--fm-theme-color in
      // fantamorte.css); l'hex è solo fallback se il CSS non è caricato.
      const tone = getComputedStyle(html).getPropertyValue('--fm-theme-color').trim();
      meta.setAttribute('content', tone || '#171a20');
    }
  }
  window.fmToggleTheme = function () {
    const current = readPref();
    const idx = THEME_PREFS.indexOf(current);
    const next = THEME_PREFS[(idx + 1) % THEME_PREFS.length];
    applyPref(next, true);
  };

  // Usata dalla pagina profilo per tenere sincronizzato localStorage
  // con la preferenza scelta nel form (senza il quale readPref() darebbe
  // sempre precedenza al vecchio valore in localStorage).
  window.fmSetThemePreference = function (pref) {
    if (THEME_PREFS.indexOf(pref) === -1) return;
    if (pref === 'auto') {
      localStorage.removeItem('fm-theme');
    } else {
      localStorage.setItem('fm-theme', pref);
    }
    applyPref(pref, false);
  };

  applyPref(readPref(), false);
  if (mql && mql.addEventListener) {
    mql.addEventListener('change', function () {
      if (readPref() === 'auto') applyPref('auto', false);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('fmThemeBtn');
    if (btn) {
      applyPref(readPref(), false);
      btn.addEventListener('click', window.fmToggleTheme);
    }
  });

  // -------- UI behaviors (vanilla, ex-Bootstrap) --------
  // Sostituiscono il bundle Bootstrap: modal, collapse, dropdown, tab e i
  // pulsanti di chiusura (data-fm-dismiss). Attributi dichiarativi:
  //   data-fm-toggle="collapse|dropdown|tab" [data-fm-target="#id" | href="#id"]
  //   data-fm-dismiss="alert|modal|toast"
  const MODAL_ANIM_MS = 200;
  let lastFocusedBeforeModal = null;

  function openModal(modalEl) {
    if (!modalEl || modalEl.classList.contains('show')) return;
    lastFocusedBeforeModal = document.activeElement;
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop fade';
    backdrop.dataset.fmBackdropFor = modalEl.id || '';
    document.body.appendChild(backdrop);
    document.body.style.overflow = 'hidden';
    modalEl.style.display = 'block';
    modalEl.removeAttribute('aria-hidden');
    modalEl.setAttribute('aria-modal', 'true');
    // rAF: lascia applicare display:block prima di attivare le transizioni.
    requestAnimationFrame(function () {
      backdrop.classList.add('show');
      modalEl.classList.add('show');
    });
    modalEl.focus();
  }

  function closeModal(modalEl) {
    if (!modalEl || !modalEl.classList.contains('show')) return;
    modalEl.classList.remove('show');
    const backdrop = document.querySelector('.modal-backdrop');
    if (backdrop) backdrop.classList.remove('show');
    window.setTimeout(function () {
      modalEl.style.display = 'none';
      modalEl.setAttribute('aria-hidden', 'true');
      modalEl.removeAttribute('aria-modal');
      if (backdrop) backdrop.remove();
      document.body.style.overflow = '';
      if (lastFocusedBeforeModal && lastFocusedBeforeModal.focus) {
        lastFocusedBeforeModal.focus();
      }
    }, MODAL_ANIM_MS);
  }
  // API pubblica (usata da fmShowPerson).
  window.fmModal = { show: openModal, hide: closeModal };

  function closeAllDropdowns(except) {
    document.querySelectorAll('.dropdown-menu.show').forEach(function (m) {
      if (m === except) return;
      m.classList.remove('show');
      const t = m.parentElement && m.parentElement.querySelector('[data-fm-toggle="dropdown"]');
      if (t) t.setAttribute('aria-expanded', 'false');
    });
  }

  function activateTab(link) {
    const sel = link.getAttribute('data-fm-target') || link.getAttribute('href');
    if (!sel) return;
    const pane = document.querySelector(sel);
    if (!pane) return;
    const navRoot = link.closest('.nav');
    if (navRoot) {
      navRoot.querySelectorAll('.nav-link').forEach(function (l) {
        l.classList.remove('active');
        l.setAttribute('aria-selected', 'false');
      });
    }
    const content = pane.closest('.tab-content');
    if (content) {
      Array.prototype.forEach.call(content.children, function (p) {
        p.classList.remove('active', 'show');
      });
    }
    link.classList.add('active');
    link.setAttribute('aria-selected', 'true');
    pane.classList.add('active', 'show');
  }

  // Toggle dichiarativi (collapse / dropdown / tab).
  document.addEventListener('click', function (e) {
    const toggle = e.target.closest('[data-fm-toggle]');
    if (toggle) {
      const kind = toggle.getAttribute('data-fm-toggle');
      if (kind === 'collapse') {
        e.preventDefault();
        const sel = toggle.getAttribute('data-fm-target');
        const target = sel && document.querySelector(sel);
        if (target) {
          const open = target.classList.toggle('show');
          toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        return;
      }
      if (kind === 'dropdown') {
        e.preventDefault();
        const menu = toggle.parentElement && toggle.parentElement.querySelector('.dropdown-menu');
        if (menu) {
          const willOpen = !menu.classList.contains('show');
          closeAllDropdowns(willOpen ? menu : null);
          menu.classList.toggle('show', willOpen);
          toggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        }
        return;
      }
      if (kind === 'tab') {
        e.preventDefault();
        activateTab(toggle);
        return;
      }
    }
    // Chiusure (data-fm-dismiss)
    const dismiss = e.target.closest('[data-fm-dismiss]');
    if (dismiss) {
      const what = dismiss.getAttribute('data-fm-dismiss');
      if (what === 'alert') {
        const al = dismiss.closest('.alert');
        if (al) al.remove();
      } else if (what === 'modal') {
        closeModal(dismiss.closest('.modal'));
      } else if (what === 'toast') {
        hideToast(dismiss.closest('.toast'));
      }
      return;
    }
    // Click su backdrop → chiudi il modal aperto.
    if (e.target.classList && e.target.classList.contains('modal-backdrop')) {
      const open = document.querySelector('.modal.show');
      if (open) closeModal(open);
      return;
    }
    // Click fuori da un dropdown aperto → chiudi.
    if (!e.target.closest('.dropdown')) closeAllDropdowns(null);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    const openModalEl = document.querySelector('.modal.show');
    if (openModalEl) { closeModal(openModalEl); return; }
    closeAllDropdowns(null);
  });

  // -------- Toast --------
  function ensureToastContainer() {
    let c = document.querySelector('.toast-container');
    if (!c) {
      c = document.createElement('div');
      c.className = 'toast-container top-0 end-0 p-3';
      document.body.appendChild(c);
    }
    return c;
  }

  function hideToast(el) {
    if (!el) return;
    el.classList.add('hide');
    window.setTimeout(function () { el.remove(); }, 250);
  }

  window.fmToast = function (msg, kind) {
    const c = ensureToastContainer();
    const resolvedKind = kind || 'dark';
    const el = document.createElement('div');
    el.className = 'toast text-bg-' + resolvedKind;
    el.setAttribute('role', 'alert');
    const closeWhite = ['danger', 'dark', 'success', 'primary', 'info', 'warning'].indexOf(resolvedKind) !== -1;
    el.innerHTML = `<div class="d-flex align-items-center"><div class="toast-body flex-grow-1"></div>
      <button type="button" class="btn-close${closeWhite ? ' btn-close-white' : ''} me-2" data-fm-dismiss="toast" aria-label="Chiudi"></button></div>`;
    el.querySelector('.toast-body').textContent = msg;
    c.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('show'); });
    window.setTimeout(function () { hideToast(el); }, 5000);
  };

  // -------- PWA install prompt + onboarding --------
  const ua = navigator.userAgent || '';
  // iPadOS 13+ si maschera da desktop: riconoscilo dal touch su piattaforma Mac.
  const isIos = /iphone|ipad|ipod/i.test(ua)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isAndroid = /android/i.test(ua);
  function isStandalone() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
      || window.navigator.standalone === true;
  }
  function pushSupported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }
  function pushPerm() {
    return ('Notification' in window) ? Notification.permission : 'unsupported';
  }

  // Evento nativo di installabilità (Android/desktop Chromium). Su iOS non arriva.
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    const btn = document.getElementById('fmInstallBtn');
    if (btn) btn.style.display = '';
    updateOnboardInstallUi();
  });

  // Chiede al browser di installare. Ritorna 'accepted' | 'dismissed' | 'unavailable'.
  window.fmTriggerInstall = async function () {
    if (!deferredPrompt) return 'unavailable';
    deferredPrompt.prompt();
    const choice = await deferredPrompt.userChoice;
    deferredPrompt = null;
    return (choice && choice.outcome === 'accepted') ? 'accepted' : 'dismissed';
  };

  window.addEventListener('appinstalled', function () {
    deferredPrompt = null;
    const btn = document.getElementById('fmInstallBtn');
    if (btn) btn.style.display = 'none';
    updateOnboardInstallUi();
    maybeCloseOnboardIfDone();
  });

  document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('fmInstallBtn');
    if (btn) {
      if (isStandalone()) {
        btn.style.display = 'none';
      } else if (isIos) {
        // iOS Safari non supporta beforeinstallprompt: istruzioni manuali.
        btn.style.display = '';
        btn.addEventListener('click', function () {
          window.fmToast('Per installare: tocca Condividi in Safari e poi «Aggiungi a Home».', 'info');
        });
      } else {
        btn.addEventListener('click', async function () {
          const r = await window.fmTriggerInstall();
          if (r === 'accepted') { window.fmToast('App installata!', 'success'); btn.style.display = 'none'; }
          else if (r === 'unavailable') { window.fmToast('Apri il menu del browser e scegli «Installa app».', 'info'); }
        });
      }
    }
    initOnboarding();
  });

  // -------- Onboarding: proponi installazione + notifiche (una tantum) --------
  // Non insiste se le procedure sono già fatte (app installata / permesso
  // notifiche concesso) né dopo un rifiuto esplicito ("Non mostrare più").
  const ONBOARD_KEY = 'fm_onboard';       // JSON: { dismissed:bool, seen:<ms> }
  const ONBOARD_SNOOZE_DAYS = 7;          // se ignorato, ripropone dopo N giorni

  function onboardState() {
    try { return JSON.parse(localStorage.getItem(ONBOARD_KEY)) || {}; }
    catch (e) { return {}; }
  }
  function saveOnboardState(s) {
    try { localStorage.setItem(ONBOARD_KEY, JSON.stringify(s)); } catch (e) { /* private mode */ }
  }

  // "Da fare": l'app non è installata e siamo su una piattaforma installabile.
  function installPending() {
    if (isStandalone()) return false;
    if (isIos || isAndroid) return true;
    return !!deferredPrompt;   // desktop: solo se il browser la dà installabile
  }
  // "Da fare": push supportate e permesso non ancora concesso.
  function pushPending() {
    if (!pushSupported()) return false;
    return pushPerm() !== 'granted';
  }

  function updateOnboardInstallUi() {
    const modal = document.getElementById('fmOnboardModal');
    if (!modal) return;
    const section = modal.querySelector('[data-fm-onboard-install]');
    const native = modal.querySelector('[data-fm-install-native]');
    const ios = modal.querySelector('[data-fm-install-ios]');
    const android = modal.querySelector('[data-fm-install-android]');
    [native, ios, android].forEach(function (el) { if (el) el.classList.add('d-none'); });
    if (!section) return;
    if (!installPending()) { section.classList.add('d-none'); return; }
    section.classList.remove('d-none');
    if (isIos) { if (ios) ios.classList.remove('d-none'); return; }
    if (deferredPrompt) { if (native) native.classList.remove('d-none'); return; }
    if (android) android.classList.remove('d-none');   // Android/altri: istruzioni manuali
  }

  function updateOnboardPushUi() {
    const modal = document.getElementById('fmOnboardModal');
    if (!modal) return;
    const section = modal.querySelector('[data-fm-onboard-push]');
    const cta = modal.querySelector('[data-fm-push-cta]');
    const iosHint = modal.querySelector('[data-fm-push-ios-hint]');
    const blocked = modal.querySelector('[data-fm-push-blocked]');
    [cta, iosHint, blocked].forEach(function (el) { if (el) el.classList.add('d-none'); });
    if (!section) return;
    if (!pushPending()) { section.classList.add('d-none'); return; }
    section.classList.remove('d-none');
    // Su iOS le push richiedono la PWA installata (16.4+): prima installa.
    if (isIos && !isStandalone()) { if (iosHint) iosHint.classList.remove('d-none'); return; }
    if (pushPerm() === 'denied') { if (blocked) blocked.classList.remove('d-none'); return; }
    if (cta) cta.classList.remove('d-none');
  }

  function maybeCloseOnboardIfDone() {
    const modal = document.getElementById('fmOnboardModal');
    if (!modal || !modal.classList.contains('show')) return;
    if (!installPending() && !pushPending()) window.fmModal.hide(modal);
  }

  function initOnboarding() {
    const modal = document.getElementById('fmOnboardModal');
    if (!modal) return;

    const installBtn = modal.querySelector('[data-fm-install-trigger]');
    if (installBtn) installBtn.addEventListener('click', async function () {
      const r = await window.fmTriggerInstall();
      if (r === 'accepted') {
        window.fmToast('App installata!', 'success');
        updateOnboardInstallUi();
        updateOnboardPushUi();   // su Android le push ora sono attivabili
        maybeCloseOnboardIfDone();
      } else if (r === 'unavailable') {
        const native = modal.querySelector('[data-fm-install-native]');
        const android = modal.querySelector('[data-fm-install-android]');
        if (native) native.classList.add('d-none');
        if (android) android.classList.remove('d-none');
      }
    });

    const pushBtn = modal.querySelector('[data-fm-push-trigger]');
    if (pushBtn) pushBtn.addEventListener('click', async function () {
      pushBtn.disabled = true;
      const ok = await window.fmEnablePush();
      pushBtn.disabled = false;
      updateOnboardPushUi();
      if (ok) maybeCloseOnboardIfDone();
    });

    const dontShow = modal.querySelector('[data-fm-onboard-dismiss]');
    if (dontShow) dontShow.addEventListener('click', function () {
      const s = onboardState();
      s.dismissed = true;
      saveOnboardState(s);
      window.fmModal.hide(modal);
    });

    updateOnboardInstallUi();
    updateOnboardPushUi();

    // Auto-apertura: solo se c'è qualcosa da proporre, mai rifiutato, non in snooze.
    const s = onboardState();
    if (s.dismissed) return;
    if (!installPending() && !pushPending()) return;
    const now = Date.now();
    if (s.seen && (now - s.seen) < ONBOARD_SNOOZE_DAYS * 86400000) return;
    s.seen = now;
    saveOnboardState(s);
    window.setTimeout(function () { window.fmModal.show(modal); }, 1200);
  }

  // -------- Service worker registration --------
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function (err) {
        console.warn('SW registration failed', err);
      });
    });
  }

  // -------- Push subscription helpers --------
  function urlBase64ToUint8Array(base64) {
    const padding = '='.repeat((4 - (base64.length % 4)) % 4);
    const b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b64);
    const arr = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return arr;
  }

  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '=([^;]+)');
    return m ? decodeURIComponent(m[2]) : '';
  }

  window.fmEnablePush = async function () {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      window.fmToast('Notifiche push non supportate da questo browser.', 'warning');
      return false;
    }
    const vapidKey = window.FM_VAPID_PUBLIC_KEY;
    if (!vapidKey) {
      window.fmToast('VAPID key non configurata sul server.', 'warning');
      return false;
    }
    try {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') {
        window.fmToast('Permesso notifiche negato.', 'warning');
        return false;
      }
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidKey),
        });
      }
      const resp = await fetch('/api/push/subscribe/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify(sub.toJSON()),
      });
      if (!resp.ok) throw new Error('subscribe fallita');
      window.fmToast('Notifiche push attivate.', 'success');
      return true;
    } catch (err) {
      console.error(err);
      window.fmToast('Errore attivazione notifiche.', 'danger');
      return false;
    }
  };

  window.fmDisablePush = async function () {
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await fetch('/api/push/unsubscribe/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          body: JSON.stringify({ endpoint: sub.endpoint }),
        });
        await sub.unsubscribe();
      }
      window.fmToast('Notifiche push disattivate su questo dispositivo.', 'info');
      return true;
    } catch (err) {
      console.error(err);
      window.fmToast('Errore disattivazione notifiche.', 'danger');
      return false;
    }
  };

  // Riflette lo stato reale della subscription push nell'interruttore master.
  window.fmSyncPushSwitch = async function () {
    const sw = document.querySelector('[data-fm-push-switch]');
    if (!sw) return;
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      sw.checked = false;
      sw.disabled = true;
      return;
    }
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      sw.checked = !!sub;
    } catch (err) {
      sw.checked = false;
    }
  };

  // Autosave preferenze (tema + matrice categoria×canale). Ritorna true su ok.
  window.fmSavePreference = async function (payload) {
    try {
      const resp = await fetch('/api/profilo/preferenze/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(function () { return {}; });
      return resp.ok && data.status === 'ok';
    } catch (err) {
      return false;
    }
  };

  // Aggiorna il badge campanella (notifiche non lette) senza ricaricare.
  window.fmUpdateNotifBadge = async function () {
    const badge = document.querySelector('[data-fm-bell-badge]');
    if (!badge) return;
    try {
      const resp = await fetch('/api/notifications/unread-count/');
      const data = await resp.json();
      const c = data.count || 0;
      badge.textContent = c;
      badge.classList.toggle('d-none', c === 0);
    } catch (err) { /* best-effort */ }
  };

  // Ricontrolla il badge quando la scheda torna visibile (es. dopo una push).
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') window.fmUpdateNotifBadge();
  });
  // ...e quando il service worker segnala una push arrivata a tab aperta.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (e) {
      if (e.data && e.data.type === 'fm-notification') window.fmUpdateNotifBadge();
    });
  }

  window.fmTestPush = async function () {
    try {
      const resp = await fetch('/api/push/test/', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
      });
      const data = await resp.json();
      if (data.success) {
        window.fmToast(`Push inviata a ${data.sent}/${data.total} dispositivi.`, 'success');
      } else {
        window.fmToast(data.error || 'Errore invio test.', 'warning');
      }
    } catch (err) {
      window.fmToast('Errore di rete.', 'danger');
    }
  };

  // -------- Pannello dettagli persona --------
  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  window.fmEscapeHtml = escapeHtml;

  function nl2br(s) {
    return escapeHtml(s).replace(/\n/g, '<br>');
  }

  // Guardia anti-race: se l'utente apre un'altra persona mentre una
  // risposta è in volo, la risposta vecchia non deve sovrascrivere il modal.
  let currentPersonPk = null;

  function renderSummaryBlock(summaryText) {
    return summaryText
      ? `<h6 class="mt-3">Biografia</h6><p class="fm-preline">${nl2br(summaryText)}</p>`
      : '<p class="text-body-secondary small">(Nessuna biografia disponibile.)</p>';
  }

  // Blocco "bonus potenziali": presente solo se il server ha ricevuto il
  // contesto lega (?league=) e la persona è viva.
  function renderPotentialBonuses(p) {
    if (!Array.isArray(p.potential_bonuses)) return '';
    var items = ['<span class="badge text-bg-secondary">Punti base +' + p.base_points + '</span>'];
    p.potential_bonuses.forEach(function (b) {
      items.push('<span class="badge text-bg-info">' + escapeHtml(b.name) +
        ' ' + (b.points >= 0 ? '+' : '') + b.points + '</span>');
    });
    return '<div class="mt-3 pt-2 border-top">' +
      '<h6 class="mb-1">Se morisse oggi <small class="text-body-secondary fw-normal">— ' +
      escapeHtml(p.league_name) + '</small></h6>' +
      '<div class="d-flex flex-wrap gap-1">' + items.join(' ') + '</div>' +
      '<div class="small text-body-secondary mt-1">Bonus automatici rilevati da Wikidata/età; ' +
      'esclusi quelli manuali e speciali, più eventuali moltiplicatori di squadra.</div>' +
      '</div>';
  }

  window.fmShowPerson = async function (pk, leagueSlug) {
    const modalEl = document.getElementById('fmPersonModal');
    if (!modalEl) return;
    currentPersonPk = pk;
    const label = document.getElementById('fmPersonModalLabel');
    const body = document.getElementById('fmPersonModalBody');
    label.textContent = 'Caricamento…';
    // Skeleton (bootstrap placeholder) al posto dello spinner nudo.
    const skeleton = document.getElementById('fmPersonSkeleton');
    body.innerHTML = '';
    if (skeleton) {
      body.appendChild(skeleton.content.cloneNode(true));
    } else {
      body.innerHTML = '<div class="text-center text-body-secondary py-5"><div class="spinner-border" role="status"></div></div>';
    }
    window.fmModal.show(modalEl);
    try {
      const url = `/api/persona/${pk}/` +
        (leagueSlug ? `?league=${encodeURIComponent(leagueSlug)}` : '');
      const resp = await fetch(url);
      if (!resp.ok) throw new Error('persona non trovata');
      const p = await resp.json();
      if (currentPersonPk !== pk) return;
      label.textContent = p.name_it;
      const meta = [];
      if (p.birth_date) meta.push(`<li><strong>Nato/a</strong>: ${escapeHtml(p.birth_date)}</li>`);
      if (p.is_dead && p.death_date) meta.push(`<li><strong>Deceduto/a</strong>: ${escapeHtml(p.death_date)}${p.age_at_death ? ' ('+p.age_at_death+' anni)' : ''}</li>`);
      else if (!p.is_dead) meta.push('<li><span class="badge text-bg-success">Vivo/a</span></li>');
      if (p.occupation) meta.push(`<li><strong>Attività</strong>: ${escapeHtml(p.occupation)}</li>`);
      if (p.nationality) meta.push(`<li><strong>Cittadinanza</strong>: ${escapeHtml(p.nationality)}</li>`);
      const img = p.image_url
        ? `<img src="${escapeHtml(p.image_url)}" alt="Foto di ${escapeHtml(p.name_it || '')}" loading="lazy" decoding="async" class="img-fluid rounded shadow-sm">`
        : `<div class="bg-secondary text-light text-center rounded p-4" aria-hidden="true"><span class="fs-1">&#128100;</span></div>`;
      // Biografia: se il dato in cache è assente/scaduto, mostra un
      // placeholder e caricala in un secondo momento senza bloccare il modal.
      const summary = p.summary_stale
        ? `<div data-fm-summary class="placeholder-glow mt-3" aria-busy="true">
             <h6>Biografia</h6>
             <p class="mb-0"><span class="placeholder col-12"></span><span class="placeholder col-10"></span><span class="placeholder col-7"></span></p>
           </div>`
        : `<div data-fm-summary>${renderSummaryBlock(p.summary_it)}</div>`;
      const links = [];
      if (p.wikipedia_url_it) links.push(`<a href="${escapeHtml(p.wikipedia_url_it)}" target="_blank" class="btn btn-outline-secondary btn-sm">Wikipedia &rarr;</a>`);
      links.push(`<a href="${escapeHtml(p.wikidata_url)}" target="_blank" class="btn btn-outline-secondary btn-sm">Wikidata &rarr;</a>`);
      links.push(`<a href="/persona/${p.id}/" class="btn btn-link btn-sm">Pagina completa</a>`);
      body.innerHTML = `
        <div class="row">
          <div class="col-md-4">${img}</div>
          <div class="col-md-8">
            ${p.description_it ? `<p class="text-body-secondary">${escapeHtml(p.description_it)}</p>` : ''}
            <ul class="list-unstyled small mb-2">${meta.join('')}</ul>
            <div class="mb-2">${links.join(' ')}</div>
            ${renderPotentialBonuses(p)}
            ${summary}
          </div>
        </div>`;
      if (p.summary_stale) {
        try {
          const sResp = await fetch(`/api/persona/${pk}/summary/`);
          if (currentPersonPk !== pk) return;
          const s = sResp.ok ? await sResp.json() : { summary_it: p.summary_it };
          if (currentPersonPk !== pk) return;
          const box = body.querySelector('[data-fm-summary]');
          // Fetch fallito e nessun dato in cache → messaggio "non disponibile".
          if (box) box.innerHTML = renderSummaryBlock(s.summary_it || p.summary_it);
        } catch (e) {
          const box = body.querySelector('[data-fm-summary]');
          if (box && currentPersonPk === pk) box.innerHTML = renderSummaryBlock(p.summary_it);
        }
      }
    } catch (e) {
      if (currentPersonPk === pk) {
        body.innerHTML = '<div class="alert alert-danger">Impossibile caricare i dettagli.</div>';
      }
    }
  };

  // Click handler per qualsiasi elemento con data-fm-person-pk. Il contesto
  // lega (per i bonus potenziali nel modal) si eredita dal più vicino
  // antenato con data-fm-league (di norma il <main> della pagina).
  document.addEventListener('click', function (e) {
    const t = e.target.closest('[data-fm-person-pk]');
    if (!t) return;
    e.preventDefault();
    const leagueEl = t.closest('[data-fm-league]');
    window.fmShowPerson(t.dataset.fmPersonPk, leagueEl ? leagueEl.dataset.fmLeague : '');
  });

  // -------- Countdown sostituzioni --------
  // Riutilizzabile su un sottoalbero: chiamata di nuovo dopo il replace di
  // una regione del DOM (es. la rosa dopo un'aggiunta senza reload).
  window.fmInitCountdowns = function (root) {
    (root || document).querySelectorAll('[data-fm-countdown]').forEach(function (el) {
      if (el.dataset.fmCountdownInit) return;  // già attivo
      el.dataset.fmCountdownInit = '1';
      const target = parseInt(el.dataset.fmCountdown, 10);
      if (!target) return;
      let intervalId = null;
      function tick() {
        // Se l'elemento non è più nel documento (regione sostituita),
        // ferma il timer.
        if (!el.isConnected && intervalId !== null) {
          clearInterval(intervalId);
          intervalId = null;
          return;
        }
        const remaining = Math.max(0, target - Math.floor(Date.now() / 1000));
        const days = Math.floor(remaining / 86400);
        const hours = Math.floor((remaining % 86400) / 3600);
        const mins = Math.floor((remaining % 3600) / 60);
        const secs = remaining % 60;
        let str;
        if (days > 0) str = `${days}g ${hours}h ${mins}m`;
        else if (hours > 0) str = `${hours}h ${mins}m ${secs}s`;
        else str = `${mins}m ${secs}s`;
        el.textContent = str;
        if (remaining < 24 * 3600) el.classList.add('urgent');
        if (remaining <= 0) {
          el.textContent = 'Scaduto';
          if (intervalId !== null) {
            clearInterval(intervalId);
            intervalId = null;
          }
        }
      }
      tick();
      if (target - Math.floor(Date.now() / 1000) > 0) {
        intervalId = setInterval(tick, 1000);
      }
    });
  };

  document.addEventListener('DOMContentLoaded', function () {
    window.fmInitCountdowns(document);
  });

  // -------- Ricerca persona (componente condiviso) --------
  // Inizializza il partial _person_search.html: debounce 600ms, min 2
  // caratteri, AbortController per annullare le richieste obsolete,
  // warning/errori inline. onSelect({wikidata_id, name_it}) al click su
  // un risultato. Ritorna { reset() }.
  window.fmPersonSearch = function (root, opts) {
    const options = opts || {};
    const input = root.querySelector('[data-fm-role="input"]');
    const results = root.querySelector('[data-fm-role="results"]');
    const warning = root.querySelector('[data-fm-role="warning"]');
    const error = root.querySelector('[data-fm-role="error"]');
    const spinner = root.querySelector('[data-fm-role="spinner"]');
    const league = root.dataset.fmLeague || '';
    let timeout;
    let abortCtl = null;

    function hide(el) { el.classList.add('d-none'); }
    function show(el) { el.classList.remove('d-none'); }

    input.addEventListener('input', function () {
      clearTimeout(timeout);
      hide(error);
      const q = this.value.trim();
      if (q.length < 2) {
        if (abortCtl) abortCtl.abort();
        results.innerHTML = '';
        hide(spinner);
        return;
      }
      timeout = setTimeout(async () => {
        if (abortCtl) abortCtl.abort();
        abortCtl = new AbortController();
        results.innerHTML = '';
        show(spinner);
        try {
          const resp = await fetch(
            `/api/search-person/?q=${encodeURIComponent(q)}&league=${encodeURIComponent(league)}`,
            { signal: abortCtl.signal });
          const data = await resp.json();
          hide(spinner);
          if (data.warning) {
            warning.textContent = '⚠ ' + data.warning;
            show(warning);
          } else {
            hide(warning);
          }
          results.innerHTML = '';
          (data.results || []).forEach(r => {
            const a = document.createElement('a');
            a.className = 'list-group-item list-group-item-action';
            a.href = '#';
            a.textContent = r.name_it + (r.description ? ' — ' + r.description : '');
            a.addEventListener('click', e => {
              e.preventDefault();
              results.innerHTML = '';
              if (options.onSelect) options.onSelect({ wikidata_id: r.wikidata_id, name_it: r.name_it });
            });
            results.appendChild(a);
          });
        } catch (err) {
          if (err.name !== 'AbortError') {
            hide(spinner);
            error.textContent = 'Errore nella ricerca. Riprova.';
            show(error);
          }
        }
      }, 600);
    });

    return {
      reset: function () {
        input.value = '';
        results.innerHTML = '';
        hide(warning); hide(error); hide(spinner);
      },
    };
  };
})();
