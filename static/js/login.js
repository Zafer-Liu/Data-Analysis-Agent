/* Login page script — external file for CSP script-src 'self' compliance
 *
 * Flow:
 *   Register: email → send verification code → code + password → register
 *   Login:    email + password → login
 *   Reset:    email → send reset code → code + new password → login
 */

var LOGIN_I18N = {
  zh: {
    'meta.title': 'SageAgent — 登录',
    'brand.website': '智析Agent 官网',
    'brand.name': '智析Agent',
    'brand.tagline': 'INTELLIGENT ANALYSIS',
    'brand.mobile_subtitle': '智能数据分析工作台',
    'story.title_line_1': '让复杂数据，',
    'story.title_line_2': '变成清晰判断。',
    'story.title_full': '让复杂数据，变成清晰判断。',
    'story.summary': '从原始数据到可执行结论，在同一个工作区完成分析、验证与表达。',
    'trace.import': '导入',
    'trace.analyze': '分析',
    'trace.decide': '决策',
    'auth.region': '账户登录与注册',
    'auth.heading': '进入你的分析工作台',
    'auth.description': '登录或创建账户，继续处理你的数据任务。',
    'auth.tabs': '账户操作',
    'tab.login': '登录',
    'tab.register': '注册',
    'field.email': '邮箱',
    'field.password': '密码',
    'field.code': '验证码',
    'field.new_password': '新密码',
    'field.confirm_password': '确认新密码',
    'placeholder.password': '输入登录密码',
    'placeholder.code': '6 位验证码',
    'placeholder.password_new': '至少 8 个字符',
    'placeholder.registered_email': '输入注册邮箱',
    'placeholder.password_confirm': '再次输入新密码',
    'action.forgot': '忘记密码？',
    'action.login': '登录',
    'action.register': '创建账户',
    'action.reset': '重置密码',
    'action.back_login': '← 返回登录',
    'action.send_code': '发送验证码',
    'action.resend': '{seconds}s 后重发',
    'agreement.prefix': '我已阅读并同意',
    'agreement.link': '《智析Agent用户协议》',
    'agreement.dialog': '智析Agent用户协议',
    'agreement.close': '我已阅读',
    'reset.heading': '重置登录密码',
    'reset.description': '验证码 5 分钟内有效。设置新密码后，其他设备上的登录会立即失效。',
    'error.agreement': '请先阅读并同意用户协议',
    'error.invalid_email': '请输入有效邮箱',
    'error.network': '网络错误',
    'error.network_retry': '网络错误，请稍后重试',
    'error.login_required': '请输入邮箱和密码',
    'error.code_required': '请输入 6 位验证码',
    'error.password_short': '密码至少 8 个字符',
    'error.new_password_short': '新密码至少 8 个字符',
    'error.password_mismatch': '两次输入的密码不一致',
    'message.code_sent': '验证码已发送',
    'message.password_reset': '密码已重置，请使用新密码登录'
  },
  en: {
    'meta.title': 'SageAgent — Sign in',
    'brand.website': 'SageAgent website',
    'brand.name': 'SageAgent',
    'brand.tagline': 'INTELLIGENT ANALYSIS',
    'brand.mobile_subtitle': 'Intelligent analysis workspace',
    'story.title_line_1': 'Turn complex data',
    'story.title_line_2': 'into clear decisions.',
    'story.title_full': 'Turn complex data into clear decisions.',
    'story.summary': 'Move from raw data to actionable conclusions—analyze, validate, and communicate in one workspace.',
    'trace.import': 'Import',
    'trace.analyze': 'Analyze',
    'trace.decide': 'Decide',
    'auth.region': 'Sign in or create an account',
    'auth.heading': 'Enter your analysis workspace',
    'auth.description': 'Sign in or create an account to continue your data work.',
    'auth.tabs': 'Account access',
    'tab.login': 'Sign in',
    'tab.register': 'Register',
    'field.email': 'Email',
    'field.password': 'Password',
    'field.code': 'Verification code',
    'field.new_password': 'New password',
    'field.confirm_password': 'Confirm password',
    'placeholder.password': 'Enter your password',
    'placeholder.code': '6-digit code',
    'placeholder.password_new': 'At least 8 characters',
    'placeholder.registered_email': 'Enter your registered email',
    'placeholder.password_confirm': 'Enter the new password again',
    'action.forgot': 'Forgot password?',
    'action.login': 'Sign in',
    'action.register': 'Create account',
    'action.reset': 'Reset password',
    'action.back_login': '← Back to sign in',
    'action.send_code': 'Send code',
    'action.resend': 'Resend in {seconds}s',
    'agreement.prefix': 'I have read and agree to',
    'agreement.link': 'the User Agreement',
    'agreement.dialog': 'SageAgent User Agreement',
    'agreement.close': 'I have read it',
    'reset.heading': 'Reset your password',
    'reset.description': 'The code is valid for 5 minutes. Setting a new password signs you out on other devices.',
    'error.agreement': 'Please read and accept the User Agreement first.',
    'error.invalid_email': 'Enter a valid email address.',
    'error.network': 'Network error',
    'error.network_retry': 'Network error. Try again later.',
    'error.login_required': 'Enter your email and password.',
    'error.code_required': 'Enter the 6-digit verification code.',
    'error.password_short': 'Password must be at least 8 characters.',
    'error.new_password_short': 'New password must be at least 8 characters.',
    'error.password_mismatch': 'The passwords do not match.',
    'message.code_sent': 'Verification code sent',
    'message.password_reset': 'Password reset. Sign in with your new password.'
  }
};

var _loginLang = 'zh';

function loginT(key, values) {
  var text = (LOGIN_I18N[_loginLang] && LOGIN_I18N[_loginLang][key]) || LOGIN_I18N.zh[key] || key;
  Object.keys(values || {}).forEach(function(name) {
    text = text.replace('{' + name + '}', values[name]);
  });
  return text;
}

function detectLoginLanguage() {
  try {
    var saved = localStorage.getItem('baa_lang');
    if (saved === 'zh' || saved === 'en') return saved;
  } catch (_e) { /* use browser language when storage is unavailable */ }
  return String(navigator.language || '').toLowerCase().startsWith('en') ? 'en' : 'zh';
}

function applyLoginLanguage(lang) {
  _loginLang = lang === 'en' ? 'en' : 'zh';
  document.documentElement.lang = _loginLang === 'en' ? 'en' : 'zh-CN';
  document.title = loginT('meta.title');
  try { localStorage.setItem('baa_lang', _loginLang); } catch (_e) { /* non-persistent fallback */ }
  document.querySelectorAll('[data-i18n]').forEach(function(el) {
    el.textContent = loginT(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(function(el) {
    el.setAttribute('placeholder', loginT(el.getAttribute('data-i18n-placeholder')));
  });
  document.querySelectorAll('[data-i18n-aria-label]').forEach(function(el) {
    el.setAttribute('aria-label', loginT(el.getAttribute('data-i18n-aria-label')));
  });
  document.querySelectorAll('[data-lang]').forEach(function(btn) {
    var active = btn.getAttribute('data-lang') === _loginLang;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

function setLoginView(id, visible) {
  var el = document.getElementById(id);
  el.hidden = !visible;
  el.style.display = visible ? '' : 'none';
}

function switchTab(tab) {
  var isLogin = tab === 'login';
  setLoginView('form-login', isLogin);
  setLoginView('form-register', !isLogin);
  setLoginView('form-reset', false);
  setLoginView('auth-tabs', true);
  setLoginView('agreement-row', true);
  document.getElementById('tab-login').classList.toggle('active', isLogin);
  document.getElementById('tab-register').classList.toggle('active', !isLogin);
  document.getElementById('tab-login').setAttribute('aria-selected', String(isLogin));
  document.getElementById('tab-register').setAttribute('aria-selected', String(!isLogin));
}

function showPasswordReset() {
  var loginEmail = document.getElementById('login-email').value.trim();
  var registerEmail = document.getElementById('reg-email').value.trim();
  setLoginView('form-login', false);
  setLoginView('form-register', false);
  setLoginView('form-reset', true);
  setLoginView('auth-tabs', false);
  setLoginView('agreement-row', false);
  document.getElementById('reset-error').textContent = '';
  document.getElementById('reset-error').classList.remove('success');
  if (!document.getElementById('reset-email').value) {
    document.getElementById('reset-email').value = loginEmail || registerEmail;
  }
  document.getElementById('reset-email').focus();
}

function restoreActionButton(btn, labelKey) {
  btn.textContent = '';
  var labelEl = document.createElement('span');
  labelEl.setAttribute('data-i18n', labelKey);
  labelEl.textContent = loginT(labelKey);
  var arrowEl = document.createElement('span');
  arrowEl.setAttribute('aria-hidden', 'true');
  arrowEl.textContent = '→';
  btn.appendChild(labelEl);
  btn.appendChild(arrowEl);
}

/* ---- send verification code + countdown ---- */

var _countdownTimers = {};

function startCountdown(buttonId, seconds) {
  var btn = document.getElementById(buttonId);
  var remaining = seconds;
  btn.disabled = true;
  btn.textContent = loginT('action.resend', {seconds: remaining});
  clearInterval(_countdownTimers[buttonId]);
  _countdownTimers[buttonId] = setInterval(function() {
    remaining--;
    if (remaining <= 0) {
      clearInterval(_countdownTimers[buttonId]);
      delete _countdownTimers[buttonId];
      btn.disabled = false;
      btn.textContent = loginT('action.send_code');
    } else {
      btn.textContent = loginT('action.resend', {seconds: remaining});
    }
  }, 1000);
}

async function sendCode() {
  var btn = document.getElementById('send-code-btn');
  var email = document.getElementById('reg-email').value.trim();
  if (!email || email.indexOf('@') === -1) {
    document.getElementById('reg-error').textContent = loginT('error.invalid_email');
    return;
  }
  document.getElementById('reg-error').textContent = '';
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/send-code', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email})
    });
    var d = await r.json();
    if (d.error) {
      document.getElementById('reg-error').textContent = d.error;
      btn.disabled = false;
      btn.textContent = loginT('action.send_code');
      return;
    }
    startCountdown('send-code-btn', 60);
  } catch (_e) {
    document.getElementById('reg-error').textContent = loginT('error.network');
    btn.disabled = false;
    btn.textContent = loginT('action.send_code');
  }
}

async function sendResetCode() {
  var btn = document.getElementById('reset-send-code-btn');
  var email = document.getElementById('reset-email').value.trim();
  var errEl = document.getElementById('reset-error');
  errEl.classList.remove('success');
  if (!email || email.indexOf('@') === -1) {
    errEl.textContent = loginT('error.invalid_email');
    return;
  }
  errEl.textContent = '';
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/password-reset/send-code', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email})
    });
    var d = await r.json();
    if (d.error) {
      errEl.textContent = d.error;
      btn.disabled = false;
      btn.textContent = loginT('action.send_code');
      return;
    }
    errEl.textContent = d.message || loginT('message.code_sent');
    errEl.classList.add('success');
    startCountdown('reset-send-code-btn', 60);
    document.getElementById('reset-code').focus();
  } catch (_e) {
    errEl.textContent = loginT('error.network_retry');
    btn.disabled = false;
    btn.textContent = loginT('action.send_code');
  }
}

/* ---- invitation referral code (from ?ref= on the invite link) ---- */

var _referralCode = '';
try {
  var _refParam = new URLSearchParams(window.location.search).get('ref');
  if (_refParam && /^[A-Za-z0-9]{4,16}$/.test(_refParam)) _referralCode = _refParam;
} catch (_e) { /* older browsers: register without referral */ }

/* ---- agreement check ---- */

function isAgreed() {
  var cb = document.getElementById('agree-check');
  if (cb && !cb.checked) {
    alert(loginT('error.agreement'));
    return false;
  }
  return true;
}

/* ---- login ---- */

async function doLogin() {
  if (!isAgreed()) return;
  var btn = document.getElementById('login-btn');
  var errEl = document.getElementById('login-error');
  var email = document.getElementById('login-email').value.trim();
  var password = document.getElementById('login-password').value;
  errEl.textContent = '';
  errEl.classList.remove('success');
  if (!email || !password) { errEl.textContent = loginT('error.login_required'); return; }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, password: password})
    });
    var d = await r.json();
    if (d.error) { errEl.textContent = d.error; btn.disabled = false; restoreActionButton(btn, 'action.login'); return; }
    window.location.href = '/';
  } catch (_e) {
    errEl.textContent = loginT('error.network'); btn.disabled = false; restoreActionButton(btn, 'action.login');
  }
}

/* ---- register ---- */

async function doRegister() {
  if (!isAgreed()) return;
  var btn = document.getElementById('reg-btn');
  var errEl = document.getElementById('reg-error');
  var email = document.getElementById('reg-email').value.trim();
  var code = document.getElementById('reg-code').value.trim();
  var password = document.getElementById('reg-password').value;
  errEl.textContent = '';
  if (!email || email.indexOf('@') === -1) { errEl.textContent = loginT('error.invalid_email'); return; }
  if (!code || code.length !== 6) { errEl.textContent = loginT('error.code_required'); return; }
  if (password.length < 8) { errEl.textContent = loginT('error.password_short'); return; }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/register', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, code: code, password: password, ref: _referralCode})
    });
    var d = await r.json();
    if (d.error) { errEl.textContent = d.error; btn.disabled = false; restoreActionButton(btn, 'action.register'); return; }
    window.location.href = '/';
  } catch (_e) {
    errEl.textContent = loginT('error.network'); btn.disabled = false; restoreActionButton(btn, 'action.register');
  }
}

/* ---- password reset ---- */

async function doPasswordReset() {
  var btn = document.getElementById('reset-btn');
  var errEl = document.getElementById('reset-error');
  var email = document.getElementById('reset-email').value.trim();
  var code = document.getElementById('reset-code').value.trim();
  var password = document.getElementById('reset-password').value;
  var confirmation = document.getElementById('reset-password-confirm').value;
  errEl.textContent = '';
  errEl.classList.remove('success');
  if (!email || email.indexOf('@') === -1) { errEl.textContent = loginT('error.invalid_email'); return; }
  if (!/^\d{6}$/.test(code)) { errEl.textContent = loginT('error.code_required'); return; }
  if (password.length < 8) { errEl.textContent = loginT('error.new_password_short'); return; }
  if (password !== confirmation) { errEl.textContent = loginT('error.password_mismatch'); return; }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/password-reset', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, code: code, password: password})
    });
    var d = await r.json();
    if (d.error) {
      errEl.textContent = d.error;
      btn.disabled = false;
      restoreActionButton(btn, 'action.reset');
      return;
    }
    document.getElementById('login-email').value = email;
    document.getElementById('login-password').value = '';
    document.getElementById('reset-code').value = '';
    document.getElementById('reset-password').value = '';
    document.getElementById('reset-password-confirm').value = '';
    switchTab('login');
    var loginMessage = document.getElementById('login-error');
    loginMessage.textContent = d.message || loginT('message.password_reset');
    loginMessage.classList.add('success');
    document.getElementById('login-password').focus();
    btn.disabled = false;
    restoreActionButton(btn, 'action.reset');
  } catch (_e) {
    errEl.textContent = loginT('error.network_retry');
    btn.disabled = false;
    restoreActionButton(btn, 'action.reset');
  }
}

/* ---- event listeners ---- */

applyLoginLanguage(detectLoginLanguage());
document.querySelectorAll('[data-lang]').forEach(function(button) {
  button.addEventListener('click', function() {
    applyLoginLanguage(button.getAttribute('data-lang'));
  });
});
document.getElementById('tab-login').addEventListener('click', function() { switchTab('login'); });
document.getElementById('tab-register').addEventListener('click', function() { switchTab('register'); });
document.getElementById('send-code-btn').addEventListener('click', sendCode);
document.getElementById('forgot-password-btn').addEventListener('click', showPasswordReset);
document.getElementById('reset-back-btn').addEventListener('click', function() { switchTab('login'); });
document.getElementById('reset-send-code-btn').addEventListener('click', sendResetCode);
document.getElementById('reset-btn').addEventListener('click', doPasswordReset);
document.getElementById('login-btn').addEventListener('click', doLogin);
document.getElementById('reg-btn').addEventListener('click', doRegister);
document.getElementById('login-password').addEventListener('keydown', function(e) { if (e.key === 'Enter') doLogin(); });
document.getElementById('reg-password').addEventListener('keydown', function(e) { if (e.key === 'Enter') doRegister(); });
document.getElementById('reset-password-confirm').addEventListener('keydown', function(e) { if (e.key === 'Enter') doPasswordReset(); });

/* Visitors arriving from an invite link are new users — show the register tab. */
if (_referralCode) switchTab('register');

/* ---- agreement modal ---- */

document.getElementById('agree-link').addEventListener('click', function(e) {
  e.preventDefault();
  document.getElementById('agreement-overlay').classList.add('show');
  document.getElementById('agree-close-btn').focus();
});
function closeAgreement() {
  document.getElementById('agreement-overlay').classList.remove('show');
  document.getElementById('agree-link').focus();
}
document.getElementById('agree-close-btn').addEventListener('click', closeAgreement);
document.getElementById('agreement-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeAgreement();
});
document.getElementById('agreement-overlay').addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeAgreement();
});
