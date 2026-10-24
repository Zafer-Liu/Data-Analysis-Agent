# -*- coding: utf-8 -*-
"""
自动映射（mapping=None）修复的回归测试 —— 2026-09 图表全量巡检修复批次。

背景：多 agent 对全部 41 个图表模块做空映射扫描，暴露的共性问题分批修复，
本文件逐项锁定行为（防止回归）：

A 级（崩溃 / 方向错误）
  - Arc/Chord：_auto_col 无 exclude 时 source/target 回退到同一列 → 崩溃
  - Sunburst/Treemap：调度层的 labels/parents 猜测把父子方向弄反
    （分支已删除，依赖模块自身的层级识别）
  - Horizon：宽表 melt 的 var_name/value_name 维度互换
  - pandas 3 str dtype（df[c].dtype == object 为 False）导致模块认不出
    字符串列 → Line 宽表退化单线、Waffle/ErrorBar 空映射失败
  - Network：模块"无显式提示不加权"是有意设计，调度层自动补 weight
B 级
  - Marimekko：x/group/y 三角色由调度层自动补
C 级
  - Dot_Plot：类别轴 hint 里的 "y" 让启发式选中数值列
  - _inject_echarts：地图数据脚本（maps/china.js）必须保留原 URL
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_PROJECT = Path(__file__).resolve().parent.parent  # Charts_generation/
sys.path.insert(0, str(_PROJECT))

import chart_generate  # noqa: E402


def _gen(df, chart_type, options=None):
    """走完整调度管线（自动映射 → 模块 → 主题后处理 → QC）。"""
    return chart_generate.generate_chart(
        df=df, excel_path=None, chart_type=chart_type,
        mapping=None, options=options or {"title": chart_type},
    )


def _ok(res):
    assert res.get("success") and res.get("html"), res
    return res


# ── A 级 ─────────────────────────────────────────────────────────────────────

def test_arc_auto_resolves_distinct_source_target():
    # 列名不含 source/target 提示词，无 exclude 时两端会回退到同一 strs[0]
    df = pd.DataFrame({"来源": ["A", "B", "C", "A"], "终点": ["B", "C", "A", "C"], "流量": [5, 3, 8, 2]})
    res = _ok(_gen(df, "arc_chart"))
    assert not any("同一列" in str(w) for w in res.get("warnings", []))
    meta = res.get("meta") or {}
    assert meta.get("n_nodes", 0) >= 2 and meta.get("n_edges", 0) >= 1


def test_chord_edge_list_auto():
    # 边表形态（字符串,字符串,数值）：exclude 修复后 src/tgt 不再撞列
    df = pd.DataFrame({"src": ["A", "B", "C"], "dst": ["B", "C", "A"], "val": [4, 6, 8]})
    res = _ok(_gen(df, "chord_diagram"))
    assert (res.get("meta") or {}).get("n_nodes", 0) >= 2


def test_sunburst_treemap_auto_hierarchy_direction():
    # 空映射时模块按列顺序识别层级：首列为父级。调度层不再反向猜测。
    df = pd.DataFrame({"大类": ["数码", "数码", "服饰"], "子类": ["手机", "平板", "上衣"], "金额": [300, 120, 150]})
    res = _ok(_gen(df, "sunburst_diagram"))
    meta = res.get("meta") or {}
    assert meta.get("level_cols") == ["大类", "子类"]   # 首列是父级
    assert meta.get("values_col") == "金额"
    res = _ok(_gen(df, "treemap"))
    meta = res.get("meta") or {}
    assert meta.get("parents_col") == "大类" and meta.get("labels_col") == "子类"


def test_horizon_wide_format_melt():
    # 宽表契约：首列=月份（x 轴），其余数值列各成一个系列
    df = pd.DataFrame({"月份": ["1月", "2月", "3月", "4月"],
                       "A": [3, 5, 4, 6], "B": [2, 4, 3, 5], "C": [-1, 2, -3, 4], "D": [5, -2, 6, -4]})
    res = _ok(_gen(df, "horizon_chart"))
    meta = res.get("meta") or {}
    assert meta.get("x_col") == "月份"
    assert meta.get("series_count") == 4


def test_line_auto_wide_multi_series_pandas3_str_dtype():
    # pandas 3 下字符串列是 str dtype：宽表检测不能只看 dtype == object
    df = pd.DataFrame({"月份": ["1月", "2月", "3月", "4月"],
                       "北京": [3, 5, 4, 6], "上海": [2, 4, 3, 5],
                       "广州": [1, 2, 1.5, 3], "深圳": [5, 4, 6, 7]})
    res = _ok(_gen(df, "line_chart"))
    from charts.theme import parse_plotly_figures
    figs, _ = parse_plotly_figures(res["html"])
    named = [t for f in figs for t in f["data"] if t.get("name")]
    assert len(named) >= 3  # 四条城市线都渲染，而不是退化成单线


def test_waffle_and_errorbar_auto():
    _ok(_gen(pd.DataFrame({"渠道": ["线上", "线下", "代理"], "占比": [50, 30, 20]}), "waffle"))
    _ok(_gen(pd.DataFrame({"组": ["A", "B", "C"], "均值": [10, 20, 15], "误差": [1, 2, 1.5]}), "error_bar_chart"))


def test_network_auto_supplies_weight():
    # 模块语义：无显式 weight 提示不加权；调度层自动映射时显式补 weight
    df = pd.DataFrame({"节点1": ["A", "B", "C"], "节点2": ["B", "C", "A"], "权重": [3, 5, 7]})
    res = _ok(_gen(df, "network_diagram"))
    assert (res.get("meta") or {}).get("has_weight") is True


# ── B 级 ─────────────────────────────────────────────────────────────────────

def test_marimekko_auto_three_roles():
    df = pd.DataFrame({"板块": ["科技", "科技", "医药", "医药"],
                       "地区": ["美", "欧", "美", "欧"], "市值": [50, 30, 20, 10]})
    res = _ok(_gen(df, "marimekko_abs"))
    meta = res.get("meta") or {}
    assert meta.get("group_col") == "地区" and meta.get("y_col") == "市值"


# ── C 级 ─────────────────────────────────────────────────────────────────────

def test_dot_plot_auto_category_axis_is_string_col():
    # 列名无提示词时类别轴必须是字符串列（城市），而非被 "y" hint 误导成数值列
    df = pd.DataFrame({"城市": ["北京", "上海", "广州", "深圳"],
                       "指标": [80, 75, 70, 65], "对比": [70, 72, 68, 60]})
    res = _ok(_gen(df, "dot_plot"))
    assert (res.get("meta") or {}).get("y_col") == "城市"
    # QC 不把哑铃连接线的 None 分隔符当作 NULL 类别
    assert not any("空/NULL 类别" in str(w) for w in res.get("warnings", []))


def test_inject_echarts_keeps_map_data_scripts():
    # maps/china.js 是地图数据（必须留在 CDN），不能被换成 echarts 库本地外链
    html = (
        '<script src="https://assets.pyecharts.org/assets/echarts.min.js"></script>'
        '<script src="https://assets.pyecharts.org/assets/maps/china.js"></script>'
    )
    out = chart_generate._inject_echarts(html)
    assert "maps/china.js" in out
    assert out.count("/static/vendor/echarts.min.js") == 1
    assert "assets.pyecharts.org/assets/echarts.min.js" not in out


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
