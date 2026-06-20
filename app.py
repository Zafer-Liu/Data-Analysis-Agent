#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Business Analyst Agent — 自适应 Vercel & 本地环境
"""

import os
from pathlib import Path
import sys

# -------------------------------
# 自动检测并安装缺失依赖（仅本地环境）
# -------------------------------
def ensure_requirements():
    import importlib
    import subprocess

    req_file = Path(__file__).parent / "requirements.txt"
    if not req_file.exists():
        print("[WARN] requirements.txt not found, skipping dependency check.")
        return

    # ✅ 标记文件：记录上次安装时的 requirements.txt 修改时间
    stamp_file = Path(__file__).parent / ".deps_installed"
    req_mtime = req_file.stat().st_mtime
    if stamp_file.exists():
        try:
            if float(stamp_file.read_text()) >= req_mtime:
                return  # ✅ 未变动，直接跳过，耗时 <1ms
        except ValueError:
            pass  # 标记文件损坏，继续检测

    # pip包名 → import名 映射
    name_map = {
        "python-dotenv": "dotenv",
        "python-docx": "docx",
        "python-pptx": "pptx",
        "pillow": "PIL",
        "scikit-learn": "sklearn",
        "beautifulsoup4": "bs4",
        "opencv-python": "cv2",
        "psycopg2-binary": "psycopg2",
        "pyyaml": "yaml",
    }

    missing = []
    with open(req_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pip_name = line.split("==")[0].split(">=")[0].split("<=")[0]\
                           .split("~=")[0].split("!=")[0].split("[")[0].strip()
            import_name = name_map.get(pip_name.lower(), pip_name.replace("-", "_"))
            try:
                importlib.import_module(import_name)
            except ImportError:
                missing.append(pip_name)

    if missing:
        print(f"[INFO] Installing missing packages: {missing}")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install"] + missing,
            )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Some packages failed to install: {e}")
            print("[ERROR] Dependency installation is incomplete; the application will not restart.")
            raise SystemExit(1)
        print("[INFO] Installation complete. Restarting...")
        stamp_file.write_text(str(req_mtime))
        # os.execv 在 Windows 上行为不稳定，改用 subprocess 启动新进程后退出。
        try:
            subprocess.Popen([sys.executable] + sys.argv)
        except Exception as exc:
            print(f"[WARN] Auto-restart failed ({exc}), please restart manually.")
        sys.exit(0)
    else:
        stamp_file.write_text(str(req_mtime))  # ✅ 写入标记，下次直接跳过
        print("[INFO] All requirements already satisfied.")

# Vercel 环境依赖由平台管理，只在本地运行
if os.environ.get("VERCEL") != "1":
    ensure_requirements()

# -------------------------------
# 应用本地兼容性补丁
# -------------------------------
try:
    from infrastructure import local_patches
    local_patches.apply()
except ImportError:
    pass

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
from infrastructure.logging_setup import setup_logging
setup_logging(level=20)  # logging.INFO

# -------------------------------
# 启动后台清理（仅本地；Vercel 短生命周期不需要）
# -------------------------------
if not is_vercel:
    from infrastructure.cleanup import setup_cleanup
    setup_cleanup(Path(__file__).parent)

# -------------------------------
# 导入 Flask app
# -------------------------------
from api import create_app
app = create_app()

# -------------------------------
# 启动配置（统一 waitress，开发/生产同一入口）
# -------------------------------
def _serve(app, host: str, port: int):
    """统一 WSGI 入口：本地用 waitress，Linux 私有化可切 gunicorn。

    waitress ≥ 3.0 支持 chunked transfer-encoding，SSE 流式正常。
    保留 gunicorn 作为 Linux 生产备选（通过环境变量 BAA_WSGI=gunicorn 切换）。
    """
    wsgi = os.environ.get("BAA_WSGI", "waitress").lower()
    if wsgi == "gunicorn":
        # 仅 Linux 可用；本地 Windows 不走这条路径
        from gunicorn.app.base import BaseApplication

        class _GunicornApp(BaseApplication):
            def __init__(self, app, options=None):
                self.app = app
                self.options = options or {}
                super().__init__()

            def load_config(self):
                for k, v in self.options.items():
                    self.cfg.set(k.lower(), v)

            def load(self):
                return self.app

        _GunicornApp(app, {
            "bind": f"{host}:{port}",
            "workers": int(os.environ.get("BAA_WORKERS", "1")),
            "worker_class": "sync",
            "timeout": 300,        # SSE 长连接
        }).run()
    else:
        try:
            from waitress import serve as waitress_serve
        except ImportError:
            print("[ERROR] waitress 未安装，请运行 pip install waitress>=3.0")
            print("        或临时回退：BAA_WSGI=flask python app.py")
            sys.exit(1)
        waitress_serve(
            app,
            host=host,
            port=port,
            # SSE 长连接需要足够大的 send_buffer 和无 send_bytes 限制
            send_bytes=1,            # 每次发送 1 字节边界，让 chunked 立即 flush
            inbuf_overflow=1024 * 1024,
            connection_limit=100,
            channel_timeout=300,     # SSE 长连接超时
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("AGENT_PORT", 5001))
    host = os.environ.get("BAA_HOST", "0.0.0.0")
    print(f"\n  Business Analyst Agent → http://localhost:{port}\n")
    print(f"  [WSGI] {os.environ.get('BAA_WSGI', 'waitress')}  (BAA_WSGI=gunicorn 可切换)\n")
    _serve(app, host, port)
