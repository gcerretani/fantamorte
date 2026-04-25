/* Fantamorte — frontend helpers (PWA, push, theme, toast) */
(function () {
  'use strict';

  // -------- Theme (dark mode) --------
  const html = document.documentElement;
  const savedTheme = localStorage.getItem('fm-theme');
  const profileDark = html.dataset.profileDark === '1';
  const initial = savedTheme || (profileDark ? 'dark' : 'light');
  html.setAttribute('data-theme', initial);

  window.fmToggleTheme = function () {
    const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('fm-theme', next);
    const btn = document.getElementById('fmThemeBtn');
    if (btn) btn.textContent = next === 'dark' ? '☀' : '☾';
  };

  document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('fmThemeBtn');
    if (btn) {
      btn.textContent = html.getAttribute('data-theme') === 'dark' ? '☀' : '☾';
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
