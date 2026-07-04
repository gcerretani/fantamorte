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
    html.setAttribute('data-bs-theme', effective);
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
    if (meta) meta.setAttribute('content', effective === 'dark' ? '#212529' : '#f8f9fa');
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

  // -------- Toast (bootstrap.Toast nativo) --------
  function ensureToastContainer() {
    let c = document.querySelector('.toast-container');
    if (!c) {
      c = document.createElement('div');
      c.className = 'toast-container position-fixed top-0 end-0 p-3';
      document.body.appendChild(c);
    }
    return c;
  }

  window.fmToast = function (msg, kind) {
    const c = ensureToastContainer();
    const resolvedKind = kind || 'dark';
    const el = document.createElement('div');
    el.className = 'toast align-items-center text-bg-' + resolvedKind + ' border-0';
    el.setAttribute('role', 'alert');
    const closeWhite = ['danger', 'dark', 'success', 'primary'].indexOf(resolvedKind) !== -1;
    el.innerHTML = `<div class="d-flex"><div class="toast-body"></div>
      <button type="button" class="btn-close${closeWhite ? ' btn-close-white' : ''} me-2 m-auto" data-bs-dismiss="toast" aria-label="Chiudi"></button></div>`;
    el.querySelector('.toast-body').textContent = msg;
    c.appendChild(el);
    el.addEventListener('hidden.bs.toast', () => el.remove());
    bootstrap.Toast.getOrCreateInstance(el, { delay: 5000, autohide: true }).show();
  };

  // -------- PWA install prompt --------
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    const btn = document.getElementById('fmInstallBtn');
    if (btn) btn.style.display = '';
  });

  document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('fmInstallBtn');
    if (!btn) return;

    // iOS Safari non supporta beforeinstallprompt: mostra istruzioni manuali.
    const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches
      || window.navigator.standalone === true;
    if (isIos && !isStandalone) {
      btn.style.display = '';
      btn.addEventListener('click', function () {
        window.fmToast('Per installare: tocca il pulsante Condividi di Safari e poi "Aggiungi alla schermata Home".', 'info');
      });
      return;
    }

    btn.addEventListener('click', async function () {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      deferredPrompt = null;
      btn.style.display = 'none';
      if (choice.outcome === 'accepted') {
        window.fmToast('App installata!', 'success');
      }
    });
  });

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
      window.fmToast('Notifiche disattivate.', 'info');
    } catch (err) {
      console.error(err);
    }
  };

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
      : '<p class="text-muted small">(Nessuna biografia disponibile.)</p>';
  }

  window.fmShowPerson = async function (pk) {
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
      body.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border" role="status"></div></div>';
    }
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
    try {
      const resp = await fetch(`/api/persona/${pk}/`);
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
            ${p.description_it ? `<p class="text-muted">${escapeHtml(p.description_it)}</p>` : ''}
            <ul class="list-unstyled small mb-2">${meta.join('')}</ul>
            <div class="mb-2">${links.join(' ')}</div>
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

  // Click handler per qualsiasi elemento con data-fm-person-pk
  document.addEventListener('click', function (e) {
    const t = e.target.closest('[data-fm-person-pk]');
    if (!t) return;
    e.preventDefault();
    window.fmShowPerson(t.dataset.fmPersonPk);
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
