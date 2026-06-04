#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Business Analyst Agent — 自适应 Vercel & 本地环境
"""

import os
from pathlib import Path
import sys

# -------------------------------
# 自动判断运行环境
# -------------------------------
is_vercel = os.environ.get("VERCEL") == "1"

# 日志目录
log_dir = Path("/tmp/outputs/Log") if is_vercel else Path(__file__).parent / "outputs" / "Log"
os.environ.setdefault("LOG_DIR", str(log_dir))

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).parent))

# -------------------------------
# 初始化日志
# -------------------------------
from log_setup import setup_logging
setup_logging(level=20)  # logging.INFO

# -------------------------------
# 启动后台清理（仅本地；Vercel 短生命周期不需要）
# -------------------------------
if not is_vercel:
    from cleanup import setup_cleanup
    setup_cleanup(Path(__file__).parent)

# -------------------------------
# 导入 Flask app
# -------------------------------
from api import create_app
app = create_app()

# -------------------------------
# 启动配置
# -------------------------------
if __name__ == "__main__":
    # Vercel 用 PORT，本地默认 5001
    port = int(os.environ.get("PORT") or os.environ.get("AGENT_PORT", 5001))
    print(f"\n  Business Analyst Agent → http://localhost:{port}\n")
    # use_reloader=False：关闭 Flask watchdog 文件监视器。
    app.run(host="0.0.0.0", port=port, debug=not is_vercel, use_reloader=False)
