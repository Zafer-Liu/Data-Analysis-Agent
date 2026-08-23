"""
Analyze module registry.
Add new analysis modules here; the agent reads this list to know what's available.

Each entry maps analysis_id → directory name under Function/Analyze/.
Modules are loaded via importlib.util (path-based) so directory names may
contain hyphens or other characters not valid in Python identifiers.
"""
import logging
log = logging.getLogger(__name__)
import importlib.util
import os
from pathlib import Path
from typing import Any, Dict

_ANALYZE_DIR = os.path.dirname(os.path.abspath(__file__))

# analysis_id → subdirectory name (may contain hyphens)
_REGISTRY_MAP: Dict[str, str] = {
    "AB_Test_Analysis":      "AB_Test_Analysis",
    "Data_Decile_Analysis":  "Data_Decile_Analysis",
    "Decision_Tree":         "Decision_Tree",
    "K_Means":               "K-Means",
    "Logistic_Regression":   "Logistic_Regression",
    "Regression":            "Regression",
    "Sklearn_Model":         "Sklearn_Model",
    "Torch_MLP":             "Torch_MLP",
    "Univariate_Screening":  "Univariate_Screening",
    "Time_Series_ARIMA":     "Time_Series_ARIMA",
    "Time_Series_SARIMA":    "Time_Series_SARIMA",
    "Time_Series_VAR":       "Time_Series_VAR",
    "Time_Series_Prophet":   "Time_Series_Prophet",
    "Time_Series_GRU":       "Time_Series_GRU",
}


def _load_module(analysis_id: str, dir_name: str):
    """
    Load analyze.py directly from a subdirectory.
    Using analyze.py (not __init__.py) avoids relative-import issues when
    the directory name contains hyphens or other non-identifier characters.
    Each analyze.py is fully self-contained (only imports numpy/pandas).
    """
    analyze_path = os.path.join(_ANALYZE_DIR, dir_name, "analyze.py")
    if not os.path.exists(analyze_path):
        raise ImportError(f"analyze.py not found in {os.path.join(_ANALYZE_DIR, dir_name)}/")
    import sys
    module_key = f"_analyze_mod_{analysis_id}"
    spec = importlib.util.spec_from_file_location(module_key, analyze_path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = mod          # register so internal imports resolve
    spec.loader.exec_module(mod)
    return mod


def get_all() -> Dict[str, Dict[str, Any]]:
    """Return metadata for all registered analyses."""
    result: Dict[str, Dict[str, Any]] = {}
    for aid, dir_name in _REGISTRY_MAP.items():
        try:
            mod = _load_module(aid, dir_name)
            result[aid] = {
                "id":            getattr(mod, "ANALYSIS_ID",    aid),
                "name":          getattr(mod, "ANALYSIS_NAME",  aid),
                "desc":          getattr(mod, "ANALYSIS_DESC",  ""),
                "required":      getattr(mod, "REQUIRED_PARAMS", []),
                "optional":      getattr(mod, "OPTIONAL_PARAMS", []),
                "output_tables": getattr(mod, "OUTPUT_TABLES",   []),
                "run":           mod.run,
            }
        except Exception as exc:
            log.warning("[registry] 加载分析模块 '%s' 失败: %s", aid, exc)
            result[aid] = {
                "id": aid, "name": aid,
                "desc": f"(load error: {exc})", "run": None,
                "output_tables": [],
            }
    return result


def get(analysis_id: str) -> Dict[str, Any]:
    """Return a single analysis entry (raises KeyError if not found)."""
    all_analyses = get_all()
    if analysis_id not in all_analyses:
        avail = ", ".join(all_analyses.keys())
        raise KeyError(f"分析模块 '{analysis_id}' 未注册。可用模块：{avail}")
    return all_analyses[analysis_id]


def build_agent_desc() -> str:
    """Build a formatted string listing all analyses, for injection into SYSTEM_PROMPT."""
    all_analyses = get_all()
    lines = []
    for entry in all_analyses.values():
        req = ", ".join(entry.get("required", []))
        opt = ", ".join(entry.get("optional", []))
        lines.append(
            f"  {entry['id']:<30} — {entry['desc'][:100]}\n"
            f"    必填参数: {req or '无'} │ 可选参数: {opt or '无'}"
        )
    return "\n".join(lines)
