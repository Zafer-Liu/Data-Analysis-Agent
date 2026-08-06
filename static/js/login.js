/* Login page script — external file for CSP script-src 'self' compliance */

function switchTab(tab) {
  document.getElementById('form-login').style.display = tab === 'login' ? '' : 'none';
  document.getElementById('form-register').style.display = tab === 'register' ? '' : 'none';
  document.getElementById('tab-login').classList.toggle('active', tab === 'login');
  document.getElementById('tab-register').classList.toggle('active', tab === 'register');
}

async function doLogin() {
  var btn = document.getElementById('login-btn');
  var errEl = document.getElementById('login-error');
  var username = document.getElementById('login-username').value.trim();
  var password = document.getElementById('login-password').value;
  errEl.textContent = '';
  if (!username || !password) { errEl.textContent = '请输入用户名和密码'; return; }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: username, password: password})
    });
    var d = await r.json();
    if (d.error) { errEl.textContent = d.error; btn.disabled = false; btn.textContent = '登录'; return; }
    window.location.href = '/';
  } catch(e) {
    errEl.textContent = '网络错误'; btn.disabled = false; btn.textContent = '登录';
  }
}

async function doRegister() {
  var btn = document.getElementById('reg-btn');
  var errEl = document.getElementById('reg-error');
  var username = document.getElementById('reg-username').value.trim();
  var password = document.getElementById('reg-password').value;
  errEl.textContent = '';
  if (!username || !password) { errEl.textContent = '请填写用户名和密码'; return; }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/register', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: username, password: password})
    });
    var d = await r.json();
    if (d.error) { errEl.textContent = d.error; btn.disabled = false; btn.textContent = '注册'; return; }
    window.location.href = '/';
  } catch(e) {
    errEl.textContent = '网络错误'; btn.disabled = false; btn.textContent = '注册';
  }
}

document.getElementById('tab-login').addEventListener('click', function() { switchTab('login'); });
document.getElementById('tab-register').addEventListener('click', function() { switchTab('register'); });
document.getElementById('login-btn').addEventListener('click', doLogin);
document.getElementById('reg-btn').addEventListener('click', doRegister);
document.getElementById('login-password').addEventListener('keydown', function(e) { if (e.key === 'Enter') doLogin(); });
document.getElementById('reg-password').addEventListener('keydown', function(e) { if (e.key === 'Enter') doRegister(); });
