"""
Analyze module registry.
Add new analysis modules here; the agent reads this list to know what's available.
"""
import importlib
import os
from pathlib import Path
from typing import Dict, Any

# Each entry: analysis_id → module path (relative to Function/Analyze/)
_REGISTRY_MAP = {
    "Data_Decile_Analysis": "Function.Analyze.Data_Decile_Analysis",
}


def get_all() -> Dict[str, Dict[str, Any]]:
    """Return metadata for all registered analyses."""
    result = {}
    for aid, modpath in _REGISTRY_MAP.items():
        try:
            mod = importlib.import_module(modpath)
            result[aid] = {
                "id":          getattr(mod, "ANALYSIS_ID",   aid),
                "name":        getattr(mod, "ANALYSIS_NAME",  aid),
                "desc":        getattr(mod, "ANALYSIS_DESC",  ""),
                "required":    getattr(mod, "REQUIRED_PARAMS", []),
                "optional":    getattr(mod, "OPTIONAL_PARAMS", []),
                "output_tables": getattr(mod, "OUTPUT_TABLES", []),
                "run":         mod.run,
            }
        except Exception as exc:
            result[aid] = {"id": aid, "name": aid, "desc": f"(load error: {exc})", "run": None}
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
