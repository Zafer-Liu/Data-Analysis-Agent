/* ── Vanilla i18n ──────────────────────────────────────────────────
 *  window.t(key, vars?)  — translate and optionally interpolate {name}
 *  window.setLang(lang)  — switch language ('zh' | 'en')
 *  window.getLang()      — current language
 *  window.applyI18n()    — apply data-i18n* attrs to the DOM
 *
 *  data-i18n="key"         → el.textContent = t(key)
 *  data-i18n-html="key"    → el.innerHTML   = t(key)
 *  data-i18n-ph="key"      → el.placeholder = t(key)
 *  data-i18n-title="key"   → el.title       = t(key)
 * ──────────────────────────────────────────────────────────────────*/
(function () {
  const T = {
    zh: {
      // ── App ──────────────────────────────────────────────────────
      'app.title': '商业分析助手',
      'app.subtitle': 'Business Analyst Agent',
      // ── Sidebar ──────────────────────────────────────────────────
      'sidebar.datasource': '数据源',
      'sidebar.disconnected': '未连接',
      'sidebar.hint.noconn': '请上传文件或连接数据库',
      'sidebar.upload': '📂 上传 Excel / CSV',
      'sidebar.connect_db': '🗄️ 连接 SQL 数据库',
      'sidebar.disconnect': '断开连接',
      'sidebar.model': '模型',
      'sidebar.model_placeholder': '— 选择模型 —',
      'sidebar.new_chat': '✦ 新对话',
      'sidebar.save': '💾 保存',
      'sidebar.saved_sessions': '已保存对话',
      'sidebar.refresh': '↻ 刷新',
      'sidebar.loading': '加载中…',
      'sidebar.check_update': '🔄 检查更新',
      // ── Header ───────────────────────────────────────────────────
      'header.title': '💬 对话分析',
      'header.subtitle': '连接数据源开始分析',
      'header.schema': '🗂 数据结构',
      // ── Welcome ──────────────────────────────────────────────────
      'welcome.title': '开始您的数据分析',
      'welcome.desc': '连接数据库或上传 Excel，用自然语言提问，AI 会自动查询、分析并生成图表。<br>输入 <strong>/</strong> 可快速选择专属命令。',
      // ── Input ────────────────────────────────────────────────────
      'input.placeholder': '输入问题，或键入 / 选择命令…',
      'send.title': '发送 (Enter)',
      // ── Modals (shared) ──────────────────────────────────────────
      'modal.cancel': '取消',
      'modal.upload': '上传',
      'modal.connect': '连接',
      'modal.close': '关闭',
      'modal.save_btn': '保存',
      // ── Excel modal ──────────────────────────────────────────────
      'modal.excel.title': '📂 上传 Excel / CSV 文件',
      'modal.excel.label': '选择文件',
      'modal.excel.hint': '支持 .xlsx / .xls / .csv',
      // ── DB modal ─────────────────────────────────────────────────
      'modal.db.title': '🗄️ 连接 SQL 数据库',
      'modal.db.name_label': '显示名称（可选）',
      'modal.db.name_ph': '例如：生产数据库',
      'modal.db.conn_label': '连接字符串',
      'modal.db.conn_req': '必填',
      'modal.db.hint': '支持 MySQL、PostgreSQL、SQLite、SQL Server 等 SQLAlchemy 数据库',
      // ── Preview modal ────────────────────────────────────────────
      'modal.preview.title': '🗂 数据预览',
      // ── Settings modal ───────────────────────────────────────────
      'modal.settings.title': '⚙ 模型设置',
      'modal.settings.builtin': '内置模型提供商',
      'modal.settings.custom': '自定义模型',
      'modal.settings.add_custom': '＋ 添加自定义模型',
      // ── Settings fields ──────────────────────────────────────────
      'settings.api_key': 'API Key',
      'settings.api_key_ph': 'sk-… 或留空清除',
      'settings.base_url': 'Base URL',
      'settings.model': 'Model',
      'settings.ctx_window': '上下文窗口',
      'settings.ctx_ph': 'tokens，例如 64000',
      'settings.max_output': '最大输出',
      'settings.out_ph': 'tokens，例如 8192',
      'settings.thinking': '思考模式',
      'settings.thinking_label': '启用思考模式',
      'settings.configured': '已配置',
      'settings.not_configured': '未配置',
      'settings.save': '保存',
      'settings.clear': '清除',
      'settings.saving': '保存中…',
      'settings.save_ok': '保存成功 ✓',
      'settings.cleared': '已清除',
      'settings.api_key_empty': 'API Key 不能为空',
      'settings.del_custom': '删除',
      // ── Add custom form ──────────────────────────────────────────
      'add_custom.name_ph': '模型名称（显示用）',
      'add_custom.url_ph': 'API Base URL，例如 https://api.deepseek.com',
      'add_custom.model_ph': 'Model ID，例如 deepseek-chat',
      'add_custom.key_ph': 'API Key',
      'add_custom.ctx_ph': '上下文窗口（tokens，选填）',
      'add_custom.out_ph': '最大输出（tokens，选填）',
      'add_custom.think': '启用思考模式',
      // ── Save session modal ───────────────────────────────────────
      'modal.save.title': '💾 保存对话',
      'modal.save.label': '对话名称',
      'modal.save.ph': '留空则自动生成时间戳名称',
      // ── Update modal ─────────────────────────────────────────────
      'modal.update.title': '🔄 检查更新',
      'modal.update.idle': '点击"拉取更新"从 GitHub 获取最新代码',
      'modal.update.btn': '⬇ 拉取更新',
      'modal.update.restart': '⚠️ 代码已更新，请重启服务（Ctrl+C → python app.py）使改动生效。',
      // ── Dynamic messages ─────────────────────────────────────────
      'slash.header': '斜杠命令  ·  输入字母快速筛选',
      'slash.searching': '命令搜索：「{term}」',
      'slash.empty': '没有匹配「{term}」的命令',
      'slash.soon': '即将推出',
      'connected_to': '已连接: {name}',
      'src.hint.db': 'SQL 数据库',
      'src.hint.file': 'Excel / CSV 文件',
      'src.restored': '已恢复连接',
      'src.lost_suffix': '（文件缺失）',
      'src.lost_hint': '原文件已不存在，仅恢复对话历史',
      'toast.upload_ok': '文件上传成功 ✓',
      'toast.db_ok': '数据库连接成功 ✓',
      'toast.disconnected': '数据源已断开',
      'toast.loaded': '已加载「{name}」',
      'toast.saved': '已保存「{name}」✓',
      'toast.deleted': '已删除「{name}」',
      'confirm.load': '加载「{name}」？当前对话内容将被替换。',
      'confirm.delete_session': '确认删除「{name}」？此操作不可撤销。',
      'confirm.clear_builtin': '确认清除 {label} 的配置？',
      'confirm.delete_custom': '确认删除此自定义模型？',
      'sys.connected': '已加载「{name}」，可以开始提问了。',
      'sys.loaded': '已加载「{name}」',
      'preview.loading': '加载中…',
      'preview.fail': '加载失败，请确认数据源已连接',
      'preview.empty': '无可预览的数据',
      'preview.rows_partial': '{cols} 列 / 共 {total} 行（显示前 {shown} 行）',
      'preview.rows_all': '{cols} 列 · 共 {total} 行',
      'stop_note': '⬛ 已停止',
      'reasoning_toggle': '推理过程',
      'btn.uploading': '上传中…',
      'conn_err': '请输入连接字符串',
      'saved_empty': '暂无保存的对话',
      'custom_empty': '暂无自定义模型',
      'update.loading': '正在从 GitHub 下载更新包，请稍候（约 10–60 秒）…',
      'update.ok_latest': '已是最新版本，无需更新。',
      'update.ok': '更新成功！',
      'update.fail': '更新失败，请查看下方输出。',
      'update.req_fail': '请求失败：',
      'update.no_output': '(无输出)',
      'status.no_model': '未选择',
      'status.line.model': '**当前模型**　{v}',
      'status.line.src': '**数据源**　　{v}',
      'status.line.usage': '**Token 用量（本次会话）**',
      'status.line.input': '输入累计　{v} tokens',
      'status.line.output': '输出累计　{v} tokens',
      'status.line.ctx': '当前上下文　{used} / {total} tokens{pct}',
      'status.line.ctx_none': '当前上下文　{used} tokens（未配置上下文窗口）',
      'ctx.bar': '上下文 {used} / {total} ({pct}%)',
      'token.bar': '↑ {input}  ↓ {output} tokens',
      // ── Command groups ───────────────────────────────────────────
      'group.analysis': '分析',
      'group.clean': '清洗',
      'group.export': '导出',
      'group.tools': '工具',
      // ── Command descriptions ─────────────────────────────────────
      'cmd.chart.desc': '用自然语言描述想要的图表',
      'cmd.sql.desc': '直接运行 SQL 查询并展示结果',
      'cmd.decile.desc': '等频分桶 + Pareto 累计曲线',
      'cmd.tree.desc': 'ID3 / C4.5 / CART 分类模型 + ROC 曲线',
      'cmd.kmeans.desc': 'K-Means++ 无监督聚类 + 肘部法则',
      'cmd.data.desc': '查看缺失值、分布、极值等统计信息',
      'cmd.inset.desc': '补 0 / 补均值 / 补中位数',
      'cmd.winsorize.desc': '按分位数截断极端值（如 1% ~ 99%）',
      'cmd.trimming.desc': '保留指定最大值和最小值范围内的行',
      'cmd.export.desc': '将数据表导出为 Excel（需说「导出」）',
      'cmd.report.desc': '生成 Word 分析报告（需说「导出」）',
      'cmd.status.desc': '查看模型、数据源与 Token 用量',
      // ── Lang toggle ──────────────────────────────────────────────
      'lang.toggle': 'EN',
    },

    en: {
      // ── App ──────────────────────────────────────────────────────
      'app.title': 'Business Analyst',
      'app.subtitle': 'Business Analyst Agent',
      // ── Sidebar ──────────────────────────────────────────────────
      'sidebar.datasource': 'Data Source',
      'sidebar.disconnected': 'Not connected',
      'sidebar.hint.noconn': 'Upload a file or connect a database',
      'sidebar.upload': '📂 Upload Excel / CSV',
      'sidebar.connect_db': '🗄️ Connect SQL Database',
      'sidebar.disconnect': 'Disconnect',
      'sidebar.model': 'Model',
      'sidebar.model_placeholder': '— Select Model —',
      'sidebar.new_chat': '✦ New Chat',
      'sidebar.save': '💾 Save',
      'sidebar.saved_sessions': 'Saved Chats',
      'sidebar.refresh': '↻ Refresh',
      'sidebar.loading': 'Loading…',
      'sidebar.check_update': '🔄 Check Update',
      // ── Header ───────────────────────────────────────────────────
      'header.title': '💬 Conversation',
      'header.subtitle': 'Connect a data source to start',
      'header.schema': '🗂 Schema',
      // ── Welcome ──────────────────────────────────────────────────
      'welcome.title': 'Start Your Data Analysis',
      'welcome.desc': 'Connect a database or upload Excel, ask in natural language, and AI will query, analyze, and generate charts.<br>Type <strong>/</strong> to browse commands.',
      // ── Input ────────────────────────────────────────────────────
      'input.placeholder': 'Ask a question, or type / for commands…',
      'send.title': 'Send (Enter)',
      // ── Modals (shared) ──────────────────────────────────────────
      'modal.cancel': 'Cancel',
      'modal.upload': 'Upload',
      'modal.connect': 'Connect',
      'modal.close': 'Close',
      'modal.save_btn': 'Save',
      // ── Excel modal ──────────────────────────────────────────────
      'modal.excel.title': '📂 Upload Excel / CSV',
      'modal.excel.label': 'Select file',
      'modal.excel.hint': 'Supports .xlsx / .xls / .csv',
      // ── DB modal ─────────────────────────────────────────────────
      'modal.db.title': '🗄️ Connect SQL Database',
      'modal.db.name_label': 'Display name (optional)',
      'modal.db.name_ph': 'e.g. Production DB',
      'modal.db.conn_label': 'Connection string',
      'modal.db.conn_req': 'required',
      'modal.db.hint': 'MySQL, PostgreSQL, SQLite, SQL Server via SQLAlchemy',
      // ── Preview modal ────────────────────────────────────────────
      'modal.preview.title': '🗂 Data Preview',
      // ── Settings modal ───────────────────────────────────────────
      'modal.settings.title': '⚙ Model Settings',
      'modal.settings.builtin': 'Built-in Providers',
      'modal.settings.custom': 'Custom Models',
      'modal.settings.add_custom': '＋ Add Custom Model',
      // ── Settings fields ──────────────────────────────────────────
      'settings.api_key': 'API Key',
      'settings.api_key_ph': 'sk-… or leave blank to clear',
      'settings.base_url': 'Base URL',
      'settings.model': 'Model',
      'settings.ctx_window': 'Context Window',
      'settings.ctx_ph': 'tokens, e.g. 64000',
      'settings.max_output': 'Max Output',
      'settings.out_ph': 'tokens, e.g. 8192',
      'settings.thinking': 'Thinking',
      'settings.thinking_label': 'Enable thinking mode',
      'settings.configured': 'Configured',
      'settings.not_configured': 'Not set',
      'settings.save': 'Save',
      'settings.clear': 'Clear',
      'settings.saving': 'Saving…',
      'settings.save_ok': 'Saved ✓',
      'settings.cleared': 'Cleared',
      'settings.api_key_empty': 'API Key is required',
      'settings.del_custom': 'Delete',
      // ── Add custom form ──────────────────────────────────────────
      'add_custom.name_ph': 'Model name (display label)',
      'add_custom.url_ph': 'API Base URL, e.g. https://api.deepseek.com',
      'add_custom.model_ph': 'Model ID, e.g. deepseek-chat',
      'add_custom.key_ph': 'API Key',
      'add_custom.ctx_ph': 'Context window (tokens, optional)',
      'add_custom.out_ph': 'Max output (tokens, optional)',
      'add_custom.think': 'Enable thinking mode',
      // ── Save session modal ───────────────────────────────────────
      'modal.save.title': '💾 Save Conversation',
      'modal.save.label': 'Session name',
      'modal.save.ph': 'Leave blank for auto timestamp',
      // ── Update modal ─────────────────────────────────────────────
      'modal.update.title': '🔄 Check for Updates',
      'modal.update.idle': 'Click "Pull Update" to fetch the latest code from GitHub',
      'modal.update.btn': '⬇ Pull Update',
      'modal.update.restart': '⚠️ Code updated. Restart the server (Ctrl+C → python app.py) to apply.',
      // ── Dynamic messages ─────────────────────────────────────────
      'slash.header': 'Slash Commands  ·  Type to filter',
      'slash.searching': 'Search: "{term}"',
      'slash.empty': 'No commands matching "{term}"',
      'slash.soon': 'Coming soon',
      'connected_to': 'Connected: {name}',
      'src.hint.db': 'SQL Database',
      'src.hint.file': 'Excel / CSV file',
      'src.restored': 'Connection restored',
      'src.lost_suffix': ' (file missing)',
      'src.lost_hint': 'File not found — chat history restored',
      'toast.upload_ok': 'File uploaded ✓',
      'toast.db_ok': 'Database connected ✓',
      'toast.disconnected': 'Data source disconnected',
      'toast.loaded': 'Loaded "{name}"',
      'toast.saved': 'Saved "{name}" ✓',
      'toast.deleted': 'Deleted "{name}"',
      'confirm.load': 'Load "{name}"? Current chat will be replaced.',
      'confirm.delete_session': 'Delete "{name}"? This cannot be undone.',
      'confirm.clear_builtin': 'Clear configuration for {label}?',
      'confirm.delete_custom': 'Delete this custom model?',
      'sys.connected': '"{name}" loaded. You can start asking questions.',
      'sys.loaded': 'Loaded "{name}"',
      'preview.loading': 'Loading…',
      'preview.fail': 'Failed to load. Check your data source connection.',
      'preview.empty': 'No data to preview',
      'preview.rows_partial': '{cols} cols / {total} rows total (showing {shown})',
      'preview.rows_all': '{cols} cols · {total} rows',
      'stop_note': '⬛ Stopped',
      'reasoning_toggle': 'Reasoning',
      'btn.uploading': 'Uploading…',
      'conn_err': 'Please enter a connection string',
      'saved_empty': 'No saved sessions',
      'custom_empty': 'No custom models',
      'update.loading': 'Downloading update from GitHub, please wait (10–60 s)…',
      'update.ok_latest': 'Already up to date.',
      'update.ok': 'Update successful!',
      'update.fail': 'Update failed. See output below.',
      'update.req_fail': 'Request failed: ',
      'update.no_output': '(no output)',
      'status.no_model': 'Not selected',
      'status.line.model': '**Model**　{v}',
      'status.line.src': '**Data Source**　{v}',
      'status.line.usage': '**Token Usage (this session)**',
      'status.line.input': 'Total input　{v} tokens',
      'status.line.output': 'Total output　{v} tokens',
      'status.line.ctx': 'Context　{used} / {total} tokens{pct}',
      'status.line.ctx_none': 'Context　{used} tokens (context window not configured)',
      'ctx.bar': 'Context {used} / {total} ({pct}%)',
      'token.bar': '↑ {input}  ↓ {output} tokens',
      // ── Command groups ───────────────────────────────────────────
      'group.analysis': 'Analysis',
      'group.clean': 'Cleaning',
      'group.export': 'Export',
      'group.tools': 'Tools',
      // ── Command descriptions ─────────────────────────────────────
      'cmd.chart.desc': 'Describe the chart you want in natural language',
      'cmd.sql.desc': 'Execute SQL directly and show results',
      'cmd.decile.desc': 'Equal-frequency bucketing + Pareto curve',
      'cmd.tree.desc': 'ID3 / C4.5 / CART classifier + ROC curve',
      'cmd.kmeans.desc': 'K-Means++ unsupervised clustering + elbow method',
      'cmd.data.desc': 'Missing values, distributions, extremes',
      'cmd.inset.desc': 'Fill with zero / mean / median',
      'cmd.winsorize.desc': 'Clip extreme values by percentile (e.g. 1% ~ 99%)',
      'cmd.trimming.desc': 'Keep rows within specified min / max range',
      'cmd.export.desc': 'Export data tables to Excel (say "export")',
      'cmd.report.desc': 'Generate Word analysis report (say "export")',
      'cmd.status.desc': 'View model, data source, and token usage',
      // ── Lang toggle ──────────────────────────────────────────────
      'lang.toggle': '中文',
    },
  };

  let _lang = localStorage.getItem('baa_lang') || 'zh';

  window.t = function (key, vars) {
    const dict = T[_lang] || T.zh;
    let s = (key in dict) ? dict[key] : ((key in T.zh) ? T.zh[key] : key);
    if (vars) {
      for (const [k, v] of Object.entries(vars)) {
        s = s.replaceAll('{' + k + '}', v);
      }
    }
    return s;
  };

  window.getLang = function () { return _lang; };

  window.setLang = function (lang) {
    if (lang !== 'zh' && lang !== 'en') return;
    _lang = lang;
    localStorage.setItem('baa_lang', lang);
    applyI18n();
    document.dispatchEvent(new CustomEvent('langchange', { detail: { lang } }));
  };

  window.applyI18n = function () {
    document.querySelectorAll('[data-i18n]').forEach(el => {
      el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-html]').forEach(el => {
      el.innerHTML = t(el.dataset.i18nHtml);
    });
    document.querySelectorAll('[data-i18n-ph]').forEach(el => {
      el.placeholder = t(el.dataset.i18nPh);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.title = t(el.dataset.i18nTitle);
    });
    const btn = document.getElementById('lang-toggle');
    if (btn) btn.textContent = t('lang.toggle');
  };

  document.addEventListener('DOMContentLoaded', applyI18n);
})();
