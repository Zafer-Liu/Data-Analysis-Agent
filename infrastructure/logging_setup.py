"""Centralised logging configuration for Business Analyst Agent.

Call setup_logging() once at startup (app.py).  Every module that does
  import logging; log = logging.getLogger(__name__)
will automatically write to both the console and the daily rotating file.

Log directory: <content_data_root>/outputs/Log/
File pattern:  baa_YYYY-MM-DD.log   (one file per day, kept 30 days)
Active file is always named after today's date — no plain "baa.log".
"""
import datetime
import logging
import logging.handlers
import os
from pathlib import Path
from infrastructure.paths import content_data_path

class _DailyFileHandler(logging.handlers.TimedRotatingFileHandler):
    """TimedRotatingFileHandler variant with daily file named baa_YYYY-MM-DD.log"""

    def __init__(self, log_dir: Path, backup_count: int = 30, encoding: str = "utf-8"):
        self._log_dir = log_dir
        today = datetime.date.today().strftime("%Y-%m-%d")
        filename = str(log_dir / f"baa_{today}.log")
        super().__init__(
            filename=filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            delay=False,
        )
        self.suffix = "%Y-%m-%d.log"
        self.namer = self._namer

    def _namer(self, default_name: str) -> str:
        date_str = default_name.rsplit(".", 1)[-1] if "." in default_name else datetime.date.today().strftime("%Y-%m-%d")
        return str(self._log_dir / f"baa_{date_str}.log")

    def doRollover(self):
        tomorrow = datetime.date.today().strftime("%Y-%m-%d")
        self.baseFilename = str(self._log_dir / f"baa_{tomorrow}.log")
        super().doRollover()

def setup_logging(level: int = logging.INFO) -> None:
    log_dir_path = os.environ.get("LOG_DIR")
    log_dir = Path(log_dir_path) if log_dir_path else content_data_path("outputs", "Log")

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        print(f"[WARNING] Cannot create log directory {log_dir}, logs will go to stdout only.")
        log_dir = None

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    if log_dir is not None:
        file_handler = _DailyFileHandler(log_dir)
        file_handler.setFormatter(fmt)
        if not any(isinstance(h, _DailyFileHandler) for h in root.handlers):
            root.addHandler(file_handler)

    # 控制台日志始终有
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)
