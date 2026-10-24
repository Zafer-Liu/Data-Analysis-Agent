# -*- coding: utf-8 -*-
"""
注册表同步门（registry sync gate）。

参考 diagram-design 的 verify-docs-sync.py：单一事实源 + CI 门。本项目图表
信息的"事实源"是三处且必须一致，任何一方漂移都会让链路断在中间：

1. ``charts/<chart_id>/chart.py``   —— 渲染入口（chart_generate 按目录名 import）
2. ``LLM/chart_selector.py`` 的 ``_CHARTS`` 快照 —— select_chart 推荐 + 必填角色校验
3. 产品文档里的图表数量（README badge / "N 种图表"）

模块新增/删除后忘改快照 → select_chart 推荐了渲染不出来的图；忘改文档 →
对外宣传数量失真。本测试把三种漂移全部拦下。

可直接 pytest 运行，也可 ``python tests/test_registry_sync.py`` 裸跑（无 pytest
依赖时）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent          # Charts_generation/
_REPO = _PROJECT.parent.parent                              # 仓库根目录
sys.path.insert(0, str(_PROJECT))
sys.path.insert(0, str(_REPO))

from LLM.chart_selector import (  # noqa: E402
    _BOOST_TABLE,
    _CHARTS,
    _CHART_INDEX,
    _COLUMN_PATTERN_TABLE,
)

# _CHARTS 条目必须齐全且非空的字段
_REQUIRED_FIELDS = (
    "chart_id", "name", "category", "required_roles", "optional_roles",
    "data_format", "constraints", "desc",
)
_TEXT_FIELDS = ("chart_id", "name", "category", "desc", "data_format", "constraints")

# 文档里的图表数量提法（相对仓库根）。每条 = (文档, [提取数量的正则])。
# 命中的所有数字都必须等于 len(_CHARTS)，否则报 文件:行号。
_DOC_COUNT_PATTERNS = [
    ("README.md", [r"Charts-(\d+)_Types", r"(\d+)\s*种图表"]),
    ("README_EN.md", [r"Charts-(\d+)_Types"]),
    ("PRODUCT.md", [r"(\d+)\s*种图表"]),
    ("Function/Charts_generation/charts/Instruction.md", [r"从\s*(\d+)\s*种"]),
]


def _module_dirs() -> list:
    """charts/ 下所有含 chart.py 的模块目录名（= 可渲染的 chart_id 集合）。"""
    charts = _PROJECT / "charts"
    return sorted(
        p.name for p in charts.iterdir()
        if p.is_dir() and (p / "chart.py").is_file()
    )


# ── 1. 模块目录 ↔ 注册表快照 双向一致 ────────────────────────────────────────

def test_registry_covers_all_module_dirs():
    dirs = set(_module_dirs())
    ids = {c["chart_id"] for c in _CHARTS}
    problems = []
    if dirs - ids:
        problems.append(
            f"charts/ 下有模块目录但没有 _CHARTS 条目（select_chart 推荐不到、"
            f"chart_generate 也无法按目录名导入）: {sorted(dirs - ids)}；"
            "请在 LLM/chart_selector.py 的 _CHARTS 中补充同名条目"
        )
    if ids - dirs:
        problems.append(
            f"_CHARTS 有条目但 charts/ 下没有对应模块目录（推荐后渲染必失败）: "
            f"{sorted(ids - dirs)}；请删除条目或恢复模块目录"
        )
    assert not problems, "\n".join(problems)


def test_every_module_defines_generate():
    problems = []
    for name in _module_dirs():
        text = (_PROJECT / "charts" / name / "chart.py").read_text(
            encoding="utf-8", errors="replace"
        )
        if not re.search(r"def\s+generate\s*\(", text):
            problems.append(
                f"charts/{name}/chart.py 未定义模块入口 generate(df, mapping, options)"
            )
    assert not problems, "\n".join(problems)


# ── 2. 注册表条目结构完整 ────────────────────────────────────────────────────

def test_registry_entries_well_formed():
    problems = []
    seen = set()
    for c in _CHARTS:
        cid = c.get("chart_id", "<missing>")
        for f in _REQUIRED_FIELDS:
            if f not in c:
                problems.append(f"{cid}: 缺少字段 {f}")
        for f in _TEXT_FIELDS:
            v = c.get(f)
            if not isinstance(v, str) or not v.strip():
                problems.append(f"{cid}: 字段 {f} 必须是非空字符串")
        rr = c.get("required_roles")
        roles_ok = isinstance(rr, list) and bool(rr) and all(
            (isinstance(r, str) and r.strip())
            or (isinstance(r, list) and bool(r)
                and all(isinstance(x, str) and x.strip() for x in r))
            for r in rr
        )
        if not roles_ok:
            problems.append(
                f"{cid}: required_roles 必须是非空列表，元素为字符串或字符串列表"
            )
        if cid in seen:
            problems.append(f"{cid}: chart_id 重复")
        seen.add(cid)
    assert not problems, "\n".join(problems)


def test_boost_tables_reference_registered_charts():
    problems = []
    for trigger_words, chart_ids, _boost in _BOOST_TABLE:
        for cid in chart_ids:
            if cid not in _CHART_INDEX:
                problems.append(
                    f"_BOOST_TABLE 引用了未注册的 chart_id: {cid}"
                    f"（触发词示例: {trigger_words[:3]}）"
                )
    for _substrs, _threshold, cid, _boost in _COLUMN_PATTERN_TABLE:
        if cid not in _CHART_INDEX:
            problems.append(f"_COLUMN_PATTERN_TABLE 引用了未注册的 chart_id: {cid}")
    assert not problems, "\n".join(problems)


# ── 3. 文档中的图表数量与注册表一致 ─────────────────────────────────────────

def test_documented_chart_counts_match_registry():
    n = len(_CHARTS)
    problems = []
    for rel, patterns in _DOC_COUNT_PATTERNS:
        path = _REPO / rel
        if not path.is_file():
            problems.append(f"{rel}: 文档不存在（同步门清单需要更新）")
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for pat in patterns:
                for m in re.finditer(pat, line):
                    if m.group(1) != str(n):
                        problems.append(
                            f"{rel}:{lineno}: 写的是 {m.group(1)} 种，"
                            f"注册表实际 {n} 种（原文: {line.strip()[:60]}）"
                        )
    assert not problems, "\n".join(problems)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
