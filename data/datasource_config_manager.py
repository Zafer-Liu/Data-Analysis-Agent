"""Persistent storage for SQL / Google Sheets / HTTP API connection configs."""
import logging
log = logging.getLogger(__name__)

import json
import os
from pathlib import Path
from typing import Optional
from infrastructure.paths import is_cloud_managed_runtime, runtime_config_path

_CONFIG_FILE = runtime_config_path(
    "datasource_config.json", "data/datasource_config.json"
)
_CONFIG_DIR = _CONFIG_FILE.parent

_SENSITIVE_KEYS = {
    "sql": "connection_string",
    "gsheets": "creds_json",
    "api": "auth_value",
}


class DataSourceConfigManager:
    def __init__(self):
        self._configs: dict = {}
        self._load()

    def _load(self):
        if is_cloud_managed_runtime():
            self._configs = {}
            return
        if _CONFIG_FILE.exists():
            try:
                self._configs = json.loads(_CONFIG_FILE.read_text("utf-8"))
            except Exception as e:
                log.warning("[datasource_config] failed to load config, using empty: %s", e)
                self._configs = {}

    def _save(self):
        if is_cloud_managed_runtime():
            return
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(
            json.dumps(self._configs, indent=2, ensure_ascii=False), "utf-8"
        )

    def save(self, ds_type: str, config: dict):
        if is_cloud_managed_runtime():
            return
        self._configs[ds_type] = config
        self._save()

    def delete(self, ds_type: str):
        if is_cloud_managed_runtime():
            return
        self._configs.pop(ds_type, None)
        self._save()

    def get(self, ds_type: str) -> Optional[dict]:
        if is_cloud_managed_runtime():
            return None
        return self._configs.get(ds_type)

    def list_public(self) -> dict:
        """Return configs with sensitive fields replaced by has_* boolean flags."""
        if is_cloud_managed_runtime():
            return {}
        result = {}
        for ds_type, cfg in self._configs.items():
            pub = dict(cfg)
            sensitive_key = _SENSITIVE_KEYS.get(ds_type)
            if sensitive_key and sensitive_key in pub:
                pub[f"has_{sensitive_key}"] = bool(pub.pop(sensitive_key))
            result[ds_type] = pub
        return result


_mgr: Optional[DataSourceConfigManager] = None


def get_datasource_config_manager() -> DataSourceConfigManager:
    global _mgr
    if _mgr is None:
        _mgr = DataSourceConfigManager()
    return _mgr
