/**
 * Cloud Disclaimer Banner — dismissal logic
 * Only loaded on managed cloud runtimes (Railway / Vercel).
 * Works within strict CSP (no inline scripts).
 */
(function () {
  'use strict';

  function init() {
    var banner = document.getElementById('cloudBanner');
    if (!banner) return;

    // Auto-hide if dismissed in current browser session
    try {
      if (sessionStorage.getItem('cloudBannerDismissed') === '1') {
        banner.classList.add('hidden');
        return;
      }
    } catch (e) { /* sessionStorage may be blocked */ }

    // Wire up close button
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
