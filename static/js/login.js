/* Login page script — external file for CSP script-src 'self' compliance
 *
 * Flow:
 *   Register: email → send verification code → code + password → register
 *   Login:    email + password → login
 */

function switchTab(tab) {
  document.getElementById('form-login').style.display = tab === 'login' ? '' : 'none';
  document.getElementById('form-register').style.display = tab === 'register' ? '' : 'none';
  document.getElementById('tab-login').classList.toggle('active', tab === 'login');
  document.getElementById('tab-register').classList.toggle('active', tab === 'register');
}

/* ---- send verification code + countdown ---- */

var _countdownTimer = null;

function startCountdown(seconds) {
  var btn = document.getElementById('send-code-btn');
  var remaining = seconds;
  btn.disabled = true;
  btn.textContent = remaining + 's 后重发';
  clearInterval(_countdownTimer);
  _countdownTimer = setInterval(function() {
    remaining--;
    if (remaining <= 0) {
      clearInterval(_countdownTimer);
      _countdownTimer = null;
      btn.disabled = false;
      btn.textContent = '发送验证码';
    } else {
      btn.textContent = remaining + 's 后重发';
    }
  }, 1000);
}

async function sendCode() {
  var btn = document.getElementById('send-code-btn');
  var email = document.getElementById('reg-email').value.trim();
  if (!email || email.indexOf('@') === -1) {
    document.getElementById('reg-error').textContent = '请输入有效邮箱';
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
      btn.textContent = '发送验证码';
      return;
    }
    startCountdown(60);
  } catch(e) {
    document.getElementById('reg-error').textContent = '网络错误';
    btn.disabled = false;
    btn.textContent = '发送验证码';
  }
}

/* ---- agreement check ---- */

function isAgreed() {
  var cb = document.getElementById('agree-check');
  if (cb && !cb.checked) {
    alert('请先阅读并同意用户协议');
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
  if (!email || !password) { errEl.textContent = '请输入邮箱和密码'; return; }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, password: password})
    });
    var d = await r.json();
    if (d.error) { errEl.textContent = d.error; btn.disabled = false; btn.textContent = '登录'; return; }
    window.location.href = '/';
  } catch(e) {
    errEl.textContent = '网络错误'; btn.disabled = false; btn.textContent = '登录';
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
  if (!email || email.indexOf('@') === -1) { errEl.textContent = '请输入有效邮箱'; return; }
  if (!code || code.length !== 6) { errEl.textContent = '请输入 6 位验证码'; return; }
  if (password.length < 4) { errEl.textContent = '密码至少 4 个字符'; return; }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/register', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, code: code, password: password})
    });
    var d = await r.json();
    if (d.error) { errEl.textContent = d.error; btn.disabled = false; btn.textContent = '注册'; return; }
    window.location.href = '/';
  } catch(e) {
    errEl.textContent = '网络错误'; btn.disabled = false; btn.textContent = '注册';
  }
}

/* ---- event listeners ---- */

document.getElementById('tab-login').addEventListener('click', function() { switchTab('login'); });
document.getElementById('tab-register').addEventListener('click', function() { switchTab('register'); });
document.getElementById('send-code-btn').addEventListener('click', sendCode);
document.getElementById('login-btn').addEventListener('click', doLogin);
document.getElementById('reg-btn').addEventListener('click', doRegister);
document.getElementById('login-password').addEventListener('keydown', function(e) { if (e.key === 'Enter') doLogin(); });
document.getElementById('reg-password').addEventListener('keydown', function(e) { if (e.key === 'Enter') doRegister(); });

/* ---- agreement modal ---- */

document.getElementById('agree-link').addEventListener('click', function() {
  document.getElementById('agreement-overlay').classList.add('show');
});
document.getElementById('agree-close-btn').addEventListener('click', function() {
  document.getElementById('agreement-overlay').classList.remove('show');
});
document.getElementById('agreement-overlay').addEventListener('click', function(e) {
  if (e.target === this) this.classList.remove('show');
});
