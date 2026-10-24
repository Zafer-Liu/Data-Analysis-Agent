/**
 * Sidebar account row + popover menu.
 *
 * Loaded in every run mode (cloud / desktop / local). In cloud & desktop the
 * menu shows 我的额度 / 邀请好友 / 赞助支持 / 退出登录; in local CLI mode it shows a
 * single "本地运行 · 无需登录" hint.
 *
 * Works within strict CSP (no inline scripts).
 */
(function () {
  'use strict';

  var runMode = (document.body && document.body.dataset.runMode) || 'local';
  var isLocal = runMode === 'local';
  var QUOTA_REFRESH_MS = 60000;
  var _quotaTimer = null;
  var lastQuota = null;
  var sponsorConfig = null;
  var sponsorRequest = null;
  var sponsorValues = {};
  var sponsorAutoChecked = false;
  var sponsorOpenedThisSession = false;
  var activeSponsorMethod = 'wechat';

  function fmt(n) {
    if (n == null) return '--';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
  }

  function formatPercent(ratio) {
    var percent = Math.max(0, Math.min(100, (Number(ratio) || 0) * 100));
    if (percent === 0 || percent === 100) return String(Math.round(percent)) + '%';
    if (percent < 0.01) return '<0.01%';
    if (percent < 1) return percent.toFixed(2).replace(/0+$/, '').replace(/\.$/, '') + '%';
    if (percent < 10) return percent.toFixed(1).replace(/\.0$/, '') + '%';
    return String(Math.round(percent)) + '%';
  }

  function $(id) { return document.getElementById(id); }

  function isEnglish() {
    return typeof window.getLang === 'function'
      ? window.getLang() === 'en'
      : document.documentElement.lang === 'en';
  }

  /* ---- account identity + quota fetch ---- */

  function fetchMe() {
    return fetch('/api/auth/me', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .catch(function () { return null; });
  }

  function applyIdentity(data) {
    var nameEl = $('sb-account-name');
    var avatarEl = $('sb-account-avatar');
    if (data && data.authenticated && data.user) {
      var email = data.user.email || '';
      if (nameEl) nameEl.textContent = email;
      if (avatarEl) avatarEl.textContent = (email.charAt(0) || '👤').toUpperCase();
      if (data.quota) {
        lastQuota = data.quota;
        updateQuotaMini(data.quota);
      }
    }
  }

  function updateQuotaMini(q) {
    var mini = $('sb-quota-mini');
    if (!mini) return;
    var dailyLimit = Number(q.daily_limit != null ? q.daily_limit : q.limit) || 0;
    var freeRemaining = Math.max(0, dailyLimit - (Number(q.used) || 0));
    var paidBalance = Number(q.paid_model_balance != null
      ? q.paid_model_balance
      : q.bonus_remaining) || 0;
    mini.textContent = (isEnglish() ? 'Free ' : '免费 ')
      + fmt(freeRemaining)
      + (isEnglish() ? ' · Paid ' : ' · 付费 ')
      + fmt(paidBalance);
  }

  function refreshQuota() {
    if (isLocal) return;
    fetchMe().then(function (data) {
      if (data) applyIdentity(data);
    });
  }

  /* ---- popover open/close ---- */

  function toggleMenu(force) {
    var menu = $('sb-account-menu');
    var trigger = $('sb-account');
    if (!menu || !trigger) return;
    var willOpen = force == null ? menu.hidden : force;
    if (willOpen) {
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      refreshQuota();
    } else {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    }
  }

  function closeMenu() { toggleMenu(false); }

  function onDocClick(e) {
    var menu = $('sb-account-menu');
    var trigger = $('sb-account');
    if (!menu || menu.hidden) return;
    if (trigger && trigger.contains(e.target)) return;
    if (menu.contains(e.target)) return;
    closeMenu();
  }

  function onKeydown(e) {
    if (e.key === 'Escape') closeMenu();
  }

  // Keep account interactions self-contained. The account row used to rely on
  // the main chat bundle's global data-action router, so a cached/failed bundle
  // left the identity visible but made the row inert.
  function onTriggerClick(e) {
    e.preventDefault();
    e.stopPropagation();
    toggleMenu();
  }

  function onTriggerKeydown(e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    e.stopPropagation();
    toggleMenu();
  }

  function onMenuClick(e) {
    var item = e.target.closest('[data-action]');
    if (!item || !e.currentTarget.contains(item)) return;
    var action = item.dataset.action;
    var handler = {
      showQuotaDetail: showQuotaDetail,
      showInvitePanel: showInvitePanel,
      showSponsorPanel: showSponsorPanel,
      cloudLogout: logout,
    }[action];
    if (!handler) return;
    e.preventDefault();
    e.stopPropagation();
    handler();
  }

  /* ---- quota detail modal ---- */

  function showQuotaDetail() {
    closeMenu();
    if (window.BAA && window.BAA.overlay) window.BAA.overlay.openOverlay('ov-quota');
    else if (typeof window.openOverlay === 'function') window.openOverlay('ov-quota');
    fetchMe().then(renderQuotaDetail);
  }

  function renderQuotaDetail(data) {
    if (!data || !data.quota) {
      var body = $('quota-detail-body');
      if (body) body.textContent = data && data.offline ? '无法连接云端，暂无法读取额度' : '请先登录';
      return;
    }
    var q = data.quota;
    var used = Number(q.used) || 0;
    var dailyLimit = Number(q.daily_limit != null ? q.daily_limit : q.limit) || 0;
    var freeUsed = Math.min(used, dailyLimit);
    var freeRemaining = Math.max(0, dailyLimit - used);
    var paidBalance = Number(q.paid_model_balance != null
      ? q.paid_model_balance
      : q.bonus_remaining) || 0;
    var paidTotal = Math.max(
      paidBalance,
      Number(q.paid_model_total_granted) || paidBalance
    );
    var paidUsed = Math.max(
      0,
      Math.min(paidTotal, Number(q.paid_model_used) || (paidTotal - paidBalance))
    );
    var freeUsedPct = dailyLimit > 0 ? freeUsed / dailyLimit : 0;
    var paidUsedPct = paidTotal > 0 ? paidUsed / paidTotal : 0;
    var freeRing = $('quota-free-ring-fg');
    if (freeRing) {
      freeRing.style.strokeDashoffset = String(100 * (1 - freeUsedPct));
      var color = '#22c55e';
      if (freeUsedPct >= 1) color = '#ef4444';
      else if (freeUsedPct >= 0.8) color = '#f59e0b';
      freeRing.style.stroke = color;
    }
    var paidRing = $('quota-paid-ring-fg');
    if (paidRing) paidRing.style.strokeDashoffset = String(100 * (1 - paidUsedPct));
    var freePercentEl = $('quota-free-percent');
    var freeBalanceEl = $('quota-free-balance');
    var freeTotalEl = $('quota-free-total');
    var paidPercentEl = $('quota-paid-percent');
    var paidUsedSummaryEl = $('quota-paid-used');
    var paidTotalEl = $('quota-paid-total');
    if (freePercentEl) freePercentEl.textContent = formatPercent(freeUsedPct);
    if (freeBalanceEl) freeBalanceEl.textContent = freeRemaining.toLocaleString();
    if (freeTotalEl) freeTotalEl.textContent = dailyLimit.toLocaleString();
    if (paidPercentEl) paidPercentEl.textContent = formatPercent(paidUsedPct);
    if (paidUsedSummaryEl) paidUsedSummaryEl.textContent = paidUsed.toLocaleString();
    if (paidTotalEl) paidTotalEl.textContent = paidTotal.toLocaleString();
    var usedText = $('quota-detail-used-text');
    var remainEl = $('quota-detail-remaining');
    var paidUsedEl = $('quota-paid-used-text');
    var bonusText = $('quota-detail-bonus-text');
    if (usedText) usedText.textContent = freeUsed.toLocaleString();
    if (remainEl) remainEl.textContent = freeRemaining.toLocaleString();
    if (paidUsedEl) paidUsedEl.textContent = paidUsed.toLocaleString();
    if (bonusText) bonusText.textContent = paidBalance.toLocaleString();
    renderQuotaModel(q.model);
    renderQuotaReset(q.reset_at);
  }

  function renderQuotaModel(model) {
    var row = $('quota-model-row');
    if (!row) return;
    row.hidden = !model;
    if (!model) return;
    var nameEl = $('quota-model-name');
    var tierEl = $('quota-model-tier');
    var displayName = model.display_name || '免费模型';
    var publicName = model.public_model_name || '';
    if (nameEl) nameEl.textContent = publicName
      ? displayName + ' | ' + publicName
      : displayName;
    if (tierEl) tierEl.textContent = document.documentElement.lang === 'en'
      ? 'Built-in · Free'
      : '内置 · 免费';
  }

  function renderQuotaReset(resetAt) {
    var timeEl = $('quota-detail-reset');
    var zoneEl = $('quota-detail-reset-zone');
    if (!timeEl || !zoneEl) return;
    var reset = new Date(resetAt || '');
    if (Number.isNaN(reset.getTime())) {
      timeEl.textContent = '--';
      zoneEl.textContent = '本地时区';
      return;
    }
    var zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local time';
    var locale = document.documentElement.lang === 'en' ? 'en-US' : 'zh-CN';
    timeEl.textContent = new Intl.DateTimeFormat(locale, {
      month: 'short',
      day: 'numeric',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(reset);
    zoneEl.textContent = zone;
  }

  /* ---- invite modal ---- */

  function showInvitePanel() {
    closeMenu();
    if (window.BAA && window.BAA.overlay) window.BAA.overlay.openOverlay('ov-invite');
    else if (typeof window.openOverlay === 'function') window.openOverlay('ov-invite');
    var input = $('invite-link-input');
    var copyBtn = $('invite-copy-btn');
    var qrBox = $('invite-qr');
    if (input) input.value = '正在生成…';
    if (copyBtn) copyBtn.disabled = true;
    if (qrBox) qrBox.innerHTML = '<span class="invite-qr-loading">正在生成二维码…</span>';
    fetch('/api/auth/referral/code', { method: 'POST', credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('referral link request failed');
        return r.json();
      })
      .then(function (d) {
        var link = d && d.link ? new URL(d.link, window.location.origin).href : '';
        if (input) input.value = link;
        if (copyBtn) copyBtn.disabled = !link;
        renderInviteQR(link, d && d.qr_data_url);
      })
      .catch(function () {
        if (input) input.value = '邀请链接生成失败，请稍后重试';
        if (copyBtn) copyBtn.disabled = true;
        renderInviteQR('', '');
      });
    fetch('/api/auth/referral/stats', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (!s) return;
        var el;
        if ((el = $('invite-stat-total'))) el.textContent = String(s.total || 0);
        if ((el = $('invite-stat-registered'))) el.textContent = String(s.registered || 0);
        if ((el = $('invite-stat-rewarded'))) el.textContent = String(s.rewarded || 0);
      })
      .catch(function () {});
  }

  function renderInviteQR(text, dataUrl) {
    var box = $('invite-qr');
    if (!box) return;
    box.innerHTML = '';
    if (!text || !dataUrl) {
      var error = document.createElement('span');
      error.className = 'invite-qr-error';
      error.textContent = '二维码暂时无法生成，请复制邀请链接分享';
      box.appendChild(error);
      return;
    }
    var image = document.createElement('img');
    image.src = dataUrl;
    image.alt = '邀请二维码';
    image.width = 164;
    image.height = 164;
    box.appendChild(image);
  }

  function copyInviteLink() {
    var input = $('invite-link-input');
    if (!input) return;
    var text = input.value;
    copyText(text)
      .then(function () { toast('已复制'); })
      .catch(function () { toast('复制失败，请手动复制'); });
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {
        return fallbackCopyText(text);
      });
    }
    return fallbackCopyText(text);
  }

  function fallbackCopyText(text) {
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      try {
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        if (!document.execCommand('copy')) throw new Error('copy command failed');
        resolve();
      } catch (e) {
        reject(e);
      } finally {
        if (ta.parentNode) ta.parentNode.removeChild(ta);
      }
    });
  }

  function toast(msg) {
    var t = $('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(function () { t.classList.remove('show'); }, 1800);
  }

  /* ---- sponsor modal + 15-day reminder ---- */

  function tr(key, fallback) {
    if (typeof window.t === 'function') {
      var translated = window.t(key);
      if (translated && translated !== key) return translated;
    }
    return fallback;
  }

  function setSponsorStatus(message, isError) {
    var status = $('sponsor-status');
    if (!status) return;
    status.textContent = message || '';
    status.hidden = !message;
    status.classList.toggle('is-error', Boolean(isError));
  }

  function fetchSponsorInfo() {
    if (sponsorConfig) return Promise.resolve(sponsorConfig);
    if (sponsorRequest) return sponsorRequest;
    sponsorRequest = fetch('/api/auth/sponsor', {
      credentials: 'same-origin',
      cache: 'no-store',
    })
      .then(function (response) {
        if (!response.ok) throw new Error('sponsor request failed');
        return response.json();
      })
      .then(function (data) {
        if (!data || !data.methods) throw new Error('invalid sponsor response');
        sponsorConfig = data;
        sponsorRequest = null;
        return data;
      })
      .catch(function (error) {
        sponsorRequest = null;
        throw error;
      });
    return sponsorRequest;
  }

  function renderSponsorConfig(data) {
    var methods = data.methods || {};
    var wechat = methods.wechat || {};
    var alipay = methods.alipay || {};
    var usdt = methods.usdt || {};
    var usdc = methods.usdc || {};
    var wechatImage = $('sponsor-wechat-image');
    var alipayImage = $('sponsor-alipay-image');
    if (wechatImage && wechat.image_url) wechatImage.src = wechat.image_url;
    if (alipayImage && alipay.image_url) alipayImage.src = alipay.image_url;

    renderSponsorCrypto('usdt', usdt);
    renderSponsorCrypto('usdc', usdc);
    sponsorValues = {
      usdt_address: usdt.address || '',
      usdt_xko: usdt.xko_address || '',
      usdt_contract: usdt.contract || '',
      usdc_address: usdc.address || '',
      usdc_xko: usdc.xko_address || '',
      usdc_contract: usdc.contract || '',
    };
    setSponsorStatus('', false);
  }

  function renderSponsorCrypto(method, config) {
    var address = $('sponsor-' + method + '-address');
    var contract = $('sponsor-' + method + '-contract');
    var qr = $('sponsor-' + method + '-qr');
    if (address) address.textContent = config.address || '—';
    if (contract) {
      contract.textContent = config.contract || '—';
      contract.title = config.contract || '';
    }
    if (qr) {
      qr.src = config.qr_data_url || '';
      qr.hidden = !config.qr_data_url;
    }
  }

  function selectSponsorMethod(method, moveFocus) {
    var tab = document.querySelector('[data-sponsor-method="' + method + '"]');
    var panel = document.querySelector('[data-sponsor-panel="' + method + '"]');
    if (!tab || !panel) return;
    activeSponsorMethod = method;
    document.querySelectorAll('[data-sponsor-method]').forEach(function (item) {
      var active = item === tab;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll('[data-sponsor-panel]').forEach(function (item) {
      var active = item === panel;
      item.classList.toggle('active', active);
      item.hidden = !active;
    });
    if (moveFocus) tab.focus();
  }

  function openSponsorOverlay() {
    if (window.BAA && window.BAA.overlay && window.BAA.overlay.openOverlay) {
      window.BAA.overlay.openOverlay('ov-sponsor');
      return true;
    }
    if (typeof window.openOverlay === 'function') {
      window.openOverlay('ov-sponsor');
      return true;
    }
    return false;
  }

  function showSponsorPanel(options) {
    options = options || {};
    closeMenu();
    if (!options.automatic) sponsorOpenedThisSession = true;
    selectSponsorMethod(activeSponsorMethod, false);
    setSponsorStatus(tr('account.sponsor.loading', '正在加载赞助方式…'), false);
    openSponsorOverlay();
    return fetchSponsorInfo()
      .then(function (data) {
        renderSponsorConfig(data);
        return data;
      })
      .catch(function () {
        setSponsorStatus(
          tr('account.sponsor.load_error', '赞助信息加载失败，请稍后重试。'),
          true
        );
        return null;
      });
  }

  function copySponsorValue(key, button) {
    var value = sponsorValues[key] || '';
    if (!value) {
      toast(tr('account.sponsor.copy_failed', '复制失败，请手动复制'));
      return;
    }
    copyText(value)
      .then(function () {
        var copied = tr('account.sponsor.copied', '已复制');
        toast(copied);
        if (!button || button.matches('.sponsor-contract-row button')) return;
        var original = button.textContent;
        button.textContent = copied;
        button.classList.add('is-copied');
        setTimeout(function () {
          button.textContent = original;
          button.classList.remove('is-copied');
        }, 1600);
      })
      .catch(function () {
        toast(tr('account.sponsor.copy_failed', '复制失败，请手动复制'));
      });
  }

  function markAutomaticSponsorShown() {
    fetch('/api/auth/sponsor/shown', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
      .then(function (response) {
        if (!response.ok) throw new Error('sponsor shown request failed');
        if (sponsorConfig && sponsorConfig.prompt) sponsorConfig.prompt.should_show = false;
      })
      .catch(function () {});
  }

  function waitForSponsorSlot(data, attempt) {
    if (sponsorOpenedThisSession || attempt > 120) return;
    var anotherOverlay = document.querySelector('.overlay.open:not(#ov-sponsor)');
    var overlayReady = Boolean(
      (window.BAA && window.BAA.overlay && window.BAA.overlay.openOverlay) ||
      typeof window.openOverlay === 'function'
    );
    if (document.hidden || anotherOverlay || !overlayReady) {
      setTimeout(function () { waitForSponsorSlot(data, attempt + 1); }, 1000);
      return;
    }
    renderSponsorConfig(data);
    selectSponsorMethod('wechat', false);
    if (openSponsorOverlay()) {
      sponsorOpenedThisSession = true;
      markAutomaticSponsorShown();
    }
  }

  function maybeShowSponsorPrompt() {
    if (isLocal || sponsorAutoChecked) return;
    sponsorAutoChecked = true;
    fetchSponsorInfo()
      .then(function (data) {
        if (!data.prompt || !data.prompt.should_show) return;
        setTimeout(function () { waitForSponsorSlot(data, 0); }, 900);
      })
      .catch(function () {});
  }

  /* ---- logout ---- */

  function logout() {
    closeMenu();
    fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' })
      .finally(function () { window.location.href = '/login'; });
  }

  /* ---- wire up ---- */

  function init() {
    // Pre-fill identity for cloud/desktop immediately.
    if (!isLocal) {
      refreshQuota();
      _quotaTimer = setInterval(refreshQuota, QUOTA_REFRESH_MS);
      maybeShowSponsorPrompt();
    }
    // Expose actions for the legacy data-action router.
    window.BAA = window.BAA || {};
    window.BAA.account = {
      toggleMenu: toggleMenu,
      showQuotaDetail: showQuotaDetail,
      showInvitePanel: showInvitePanel,
      showSponsorPanel: showSponsorPanel,
      copyInviteLink: copyInviteLink,
      cloudLogout: logout,
    };
    document.addEventListener('click', onDocClick);
    document.addEventListener('keydown', onKeydown);
    document.addEventListener('langchange', function () {
      if (lastQuota) updateQuotaMini(lastQuota);
    });
    var trigger = $('sb-account');
    var menu = $('sb-account-menu');
    if (trigger) {
      trigger.addEventListener('click', onTriggerClick);
      trigger.addEventListener('keydown', onTriggerKeydown);
    }
    if (menu) menu.addEventListener('click', onMenuClick);
    var copyBtn = $('invite-copy-btn');
    if (copyBtn) {
      copyBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        copyInviteLink();
      });
    }
    document.querySelectorAll('[data-sponsor-method]').forEach(function (tab) {
      tab.addEventListener('click', function () {
        selectSponsorMethod(tab.dataset.sponsorMethod, false);
      });
      tab.addEventListener('keydown', function (event) {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        var tabs = Array.from(document.querySelectorAll('[data-sponsor-method]'));
        var current = tabs.indexOf(tab);
        var next = current;
        if (event.key === 'ArrowLeft') next = (current - 1 + tabs.length) % tabs.length;
        if (event.key === 'ArrowRight') next = (current + 1) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        event.preventDefault();
        selectSponsorMethod(tabs[next].dataset.sponsorMethod, true);
      });
    });
    document.querySelectorAll('[data-sponsor-copy]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        copySponsorValue(button.dataset.sponsorCopy, button);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
