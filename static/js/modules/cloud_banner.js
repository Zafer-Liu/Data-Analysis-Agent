/**
 * Cloud Disclaimer Banner + Token Quota Indicator
 * Only loaded on managed cloud runtimes (Railway / Vercel).
 * Works within strict CSP (no inline scripts).
 */
(function () {
  'use strict';

  function init() {
    initBanner();
    initQuota();
  }

  /* ---- disclaimer banner ---- */

  function initBanner() {
    var banner = document.getElementById('cloudBanner');
    if (!banner) return;

    try {
      if (sessionStorage.getItem('cloudBannerDismissed') === '1') {
        banner.classList.add('hidden');
        return;
      }
    } catch (e) { /* sessionStorage may be blocked */ }

    var closeBtn = document.getElementById('cloudBannerClose');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        banner.classList.add('hidden');
        try {
          sessionStorage.setItem('cloudBannerDismissed', '1');
        } catch (e) { /* ignore */ }
      });
    }
  }

  /* ---- token quota indicator ---- */

  function initQuota() {
    var box = document.getElementById('cloud-quota');
    if (!box) return;
    fetchQuota();
    setInterval(fetchQuota, 60000); // refresh every 60s
  }

  function fmt(n) {
    return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
  }

  function fetchQuota() {
    fetch('/api/auth/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.authenticated || !d.quota) return;
        var q = d.quota;
        var bar = document.getElementById('cloud-quota-bar');
        var txt = document.getElementById('cloud-quota-text');
        if (!bar || !txt) return;

        var pct = q.limit > 0 ? (q.used / q.limit) * 100 : 0;
        bar.style.width = Math.min(pct, 100) + '%';

        var color = '#22c55e'; // green
        if (pct >= 100) color = '#ef4444';      // red
        else if (pct >= 80) color = '#f59e0b';  // orange
        bar.style.background = color;

        txt.textContent = fmt(q.used) + ' / ' + fmt(q.limit);
      })
      .catch(function () { /* silent */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
