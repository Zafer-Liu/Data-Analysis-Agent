/**
 * Cloud Disclaimer Banner + Token Quota Indicator
 * Only loaded on managed cloud runtimes (Railway / Vercel).
 * Works within strict CSP (no inline scripts).
 */
(function () {
  'use strict';

  function init() {
    initBanner();
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
    } catch (_e) { /* sessionStorage may be blocked */ }

    var closeBtn = document.getElementById('cloudBannerClose');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        banner.classList.add('hidden');
        try {
          sessionStorage.setItem('cloudBannerDismissed', '1');
        } catch (_e) { /* ignore */ }
      });
    }
  }

  /* ---- token quota indicator moved to account_menu.js ---- */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
