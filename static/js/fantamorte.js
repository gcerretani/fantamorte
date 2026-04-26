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
    html.setAttribute('data-theme', effectiveTheme(pref));
    html.setAttribute('data-theme-pref', pref);
    if (persist) localStorage.setItem('fm-theme', pref);
    const btn = document.getElementById('fmThemeBtn');
    if (btn) {
      btn.textContent = THEME_ICONS[pref];
      const label = 'Tema: ' + THEME_LABELS[pref] + ' (clicca per cambiare)';
      btn.title = label;
      btn.setAttribute('aria-label', label);
    }
  }
  window.fmToggleTheme = function () {
    const current = readPref();
    const idx = THEME_PREFS.indexOf(current);
    const next = THEME_PREFS[(idx + 1) % THEME_PREFS.length];
    applyPref(next, true);
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

  // -------- Toast --------
  function ensureToastContainer() {
    let c = document.querySelector('.fm-toast-container');
    if (!c) {
      c = document.createElement('div');
      c.className = 'fm-toast-container';
      document.body.appendChild(c);
    }
    return c;
  }

  window.fmToast = function (msg, kind) {
    const c = ensureToastContainer();
    const el = document.createElement('div');
    el.className = 'toast align-items-center text-bg-' + (kind || 'dark') + ' border-0 show';
    el.setAttribute('role', 'alert');
    el.innerHTML = `<div class="d-flex"><div class="toast-body">${msg}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
    c.appendChild(el);
    setTimeout(() => el.remove(), 5000);
    el.querySelector('.btn-close').addEventListener('click', () => el.remove());
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

  function nl2br(s) {
    return escapeHtml(s).replace(/\n/g, '<br>');
  }

  window.fmShowPerson = async function (pk) {
    const modalEl = document.getElementById('fmPersonModal');
    if (!modalEl) return;
    const label = document.getElementById('fmPersonModalLabel');
    const body = document.getElementById('fmPersonModalBody');
    label.textContent = 'Caricamento…';
    body.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border" role="status"></div></div>';
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
    try {
      const resp = await fetch(`/api/persona/${pk}/`);
      if (!resp.ok) throw new Error('persona non trovata');
      const p = await resp.json();
      label.textContent = p.name_it;
      const meta = [];
      if (p.birth_date) meta.push(`<li><strong>Nato/a</strong>: ${escapeHtml(p.birth_date)}</li>`);
      if (p.is_dead && p.death_date) meta.push(`<li><strong>Deceduto/a</strong>: ${escapeHtml(p.death_date)}${p.age_at_death ? ' ('+p.age_at_death+' anni)' : ''}</li>`);
      else if (!p.is_dead) meta.push('<li><span class="badge bg-success">Vivo/a</span></li>');
      if (p.occupation) meta.push(`<li><strong>Attività</strong>: ${escapeHtml(p.occupation)}</li>`);
      if (p.nationality) meta.push(`<li><strong>Cittadinanza</strong>: ${escapeHtml(p.nationality)}</li>`);
      const img = p.image_url
        ? `<img src="${escapeHtml(p.image_url)}" alt="" class="img-fluid rounded shadow-sm">`
        : `<div class="bg-secondary text-light text-center rounded p-4"><span style="font-size:3rem">&#128100;</span></div>`;
      const summary = p.summary_it
        ? `<h6 class="mt-3">Biografia</h6><p style="white-space:pre-line">${nl2br(p.summary_it)}</p>`
        : '<p class="text-muted small">(Nessuna biografia disponibile.)</p>';
      const links = [];
      if (p.wikipedia_url_it) links.push(`<a href="${escapeHtml(p.wikipedia_url_it)}" target="_blank" class="btn btn-outline-dark btn-sm">Wikipedia &rarr;</a>`);
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
    } catch (e) {
      body.innerHTML = '<div class="alert alert-danger">Impossibile caricare i dettagli.</div>';
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
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-fm-countdown]').forEach(function (el) {
      const target = parseInt(el.dataset.fmCountdown, 10);
      if (!target) return;
      function tick() {
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
        if (remaining <= 0) el.textContent = 'Scaduto';
      }
      tick();
      setInterval(tick, 1000);
    });
  });
})();
