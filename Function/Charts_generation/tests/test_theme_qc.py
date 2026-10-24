# -*- coding: utf-8 -*-
"""
theme + chart_qc 单元/集成测试。

可直接 pytest 运行，也可 ``python tests/test_theme_qc.py`` 裸跑（无 pytest
依赖时）。覆盖：
1. 内嵌 Plotly.newPlot JSON 的解析-修改-回写往返（3 参/4 参/链式调用/异常形态）
2. theme 语义角色与布局铬件应用
3. 焦点规则（多系列降饱和、单系列按类别值 accent、未命中回 warning）
4. QC 检查（复杂度预算、空标签、重复系列名、单点折线、无 trace）
5. 端到端：Bar_Chart 真实产出 → postprocess → QC
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_PROJECT = Path(__file__).resolve().parent.parent  # Charts_generation/
sys.path.insert(0, str(_PROJECT))

from charts.theme import (  # noqa: E402
    apply_focus,
    normalize_highlight,
    postprocess_plotly_html,
    semantic_roles,
    style_figure,
)
from charts import chart_qc  # noqa: E402


# ── 1. JSON 重写器 ───────────────────────────────────────────────────────────

def _call(data, layout, config='{"responsive": true}'):
    d = json.dumps(data, separators=(",", ":"))
    l = json.dumps(layout, separators=(",", ":"))
    if config is None:
        return f'Plotly.newPlot("div1", {d}, {l})'
    return f'Plotly.newPlot("div1", {d}, {l}, {json.dumps(json.loads(config), separators=(",", ":"))})'


def test_roundtrip_preserves_data():
    data = [{"type": "bar", "x": ["a", "b"], "y": [1, 2], "name": "S1"}]
    layout = {"title": {"text": "T"}, "xaxis": {}}
    html = f"<html><body>{_call(data, layout)}</body></html>"
    out, figs, warns = postprocess_plotly_html(html, "mckinsey", {})
    assert not warns
    assert len(figs) == 1
    # 数据语义保持不变（主题只动铬件）
    assert figs[0]["data"][0]["x"] == ["a", "b"]
    assert figs[0]["data"][0]["y"] == [1, 2]
    # 回写后的 HTML 里 JSON 依然合法（用 python json 解析回来验证）
    assert 'Plotly.newPlot("div1",' in out
    start = out.index("(", out.index("Plotly.newPlot")) + 1
    dec = json.JSONDecoder()

    def _next_arg(pos, skip_comma=True):
        pos = out.index(",", pos) + 1 if skip_comma else pos
        while out[pos] in " \t\r\n":
            pos += 1
        return dec.raw_decode(out, pos)

    _id, end = _next_arg(start, skip_comma=False)
    parsed_data, end = _next_arg(end)
    parsed_layout, end = _next_arg(end)
    assert parsed_data[0]["x"] == ["a", "b"]
    assert parsed_layout["title"]["text"] == "T"


def test_three_arg_call_and_chained_tail():
    data = [{"type": "scatter", "mode": "lines", "x": [1, 2], "y": [3, 4]}]
    html = "<div>" + _call(data, {}, config=None) + ").then(function(){});</div>"
    out, figs, warns = postprocess_plotly_html(html, "mckinsey", {})
    assert not warns and len(figs) == 1
    assert ").then(function(){})" in out  # 链式尾部原样保留


def test_plotly_react_supported():
    data = [{"type": "bar", "x": ["a"], "y": [1]}]
    html = f'<script>Plotly.react("d", {json.dumps(data)}, {{}});</script>'
    out, figs, warns = postprocess_plotly_html(html, "mckinsey", {})
    assert not warns and len(figs) == 1
    assert 'Plotly.react("d",' in out


def test_malformed_call_left_untouched():
    bad = "Plotly.newPlot(document.getElementById('x'), data, layout);"
    html = f"<html>{bad}</html>"
    out, figs, warns = postprocess_plotly_html(html, "mckinsey", {})
    assert bad in out  # 原样保留
    assert warns  # 有解析失败提示


def _var_fig_html(data, layout, div="fig-1"):
    """pio.to_html(full_html=False) 的内嵌形态：figure 存在 var fig 字面量里。"""
    return (
        "<script>(function() { try {\n"
        f"var fig = {json.dumps({'data': data, 'layout': layout}, ensure_ascii=False)};\n"
        f"Plotly.newPlot('{div}', fig.data, fig.layout, {{responsive: true}});\n"
        "} catch(e) {} })();</script>"
    )


def test_var_fig_literal_roundtrip():
    # Sunburst/Treemap 的内嵌形态：newPlot 引用 fig.data/fig.layout
    data = [{"type": "sunburst", "ids": ["a", "b"], "values": [3, 5]}]
    layout = {"title": {"text": "T"}}
    html = _var_fig_html(data, layout)
    out, figs, warns = postprocess_plotly_html(html, "mckinsey", {"size": "square"})
    assert not warns, warns          # 引用字面量的调用不是"解析失败"
    assert len(figs) == 1
    assert figs[0]["layout"]["width"] == 800           # size 预设已应用
    assert figs[0]["layout"]["font"]["family"]          # 主题铬件已应用
    assert "fig.data, fig.layout" in out                # 调用本身原样保留
    from charts.theme import parse_plotly_figures
    figs2, warns2 = parse_plotly_figures(out)
    assert len(figs2) == 1 and not warns2               # 读不改路径同样可解析


def test_non_plotly_html_passthrough():
    html = "<html><body>echarts.init(dom);</body></html>"
    out, figs, warns = postprocess_plotly_html(html, "mckinsey", {})
    assert out == html and figs == [] and warns == []


# ── 2. 主题 ──────────────────────────────────────────────────────────────────

def test_semantic_roles():
    roles = semantic_roles("mckinsey")
    assert roles["accent"] and roles["series"] and roles["paper"] == "#ffffff"
    bcg = semantic_roles("bcg")
    assert bcg["accent"] != roles["accent"]  # accent 换肤


def test_style_figure_chrome():
    fig = {
        "data": [{"type": "bar", "x": ["a"], "y": [1]}],
        "layout": {"title": "旧标题", "xaxis": {"title": "类别"}, "yaxis2": {}},
    }
    style_figure(fig, roles=semantic_roles("mckinsey"))
    lay = fig["layout"]
    assert lay["title"]["text"] == "旧标题"
    assert lay["title"]["x"] == 0.0 and lay["title"]["xanchor"] == "left"
    assert lay["font"]["color"] == semantic_roles("mckinsey")["ink"]
    assert lay["xaxis"]["gridcolor"] == semantic_roles("mckinsey")["rule"]
    assert lay["xaxis"]["title"]["font"]["color"]  # 轴标题规范化为 dict
    assert lay["yaxis2"]["tickfont"]["color"]      # 次轴同样处理
    assert lay["colorway"] == semantic_roles("mckinsey")["series"]


# ── 3. 焦点规则 ──────────────────────────────────────────────────────────────

def test_focus_multi_series():
    fig = {"data": [
        {"type": "scatter", "name": "实际", "x": [1, 2], "y": [1, 2], "marker": {"color": "#111"}},
        {"type": "scatter", "name": "预测", "x": [1, 2], "y": [2, 3], "marker": {"color": "#222"}},
    ], "layout": {}}
    w = apply_focus(fig, "预测", roles=semantic_roles("mckinsey"))
    assert w is None
    accent = semantic_roles("mckinsey")["accent"]
    assert fig["data"][1]["marker"]["color"] == accent   # 焦点系列重着色
    assert fig["data"][0]["opacity"] == 0.3              # 非焦点降透明


def test_focus_single_series_by_category():
    fig = {"data": [{"type": "bar", "x": ["华东", "华南", "华北"], "y": [1, 2, 3]}], "layout": {}}
    w = apply_focus(fig, "华北", roles=semantic_roles("mckinsey"))
    assert w is None
    roles = semantic_roles("mckinsey")
    assert fig["data"][0]["marker"]["color"] == [roles["dim"], roles["dim"], roles["accent"]]


def test_focus_no_match_returns_warning():
    fig = {"data": [{"type": "bar", "x": ["华东", "华南"], "y": [1, 2]}], "layout": {}}
    w = apply_focus(fig, "西北", roles=semantic_roles("mckinsey"))
    assert w and "华东" in w  # warning 里带类别示例，帮 LLM 修正


def test_normalize_highlight():
    assert normalize_highlight("a, b,,a") == ["a", "b"]
    assert normalize_highlight(["x", " x "]) == ["x"]
    assert normalize_highlight("") == []


# ── 4. QC ────────────────────────────────────────────────────────────────────

def test_qc_budget_categories():
    figs = [{"data": [{"type": "bar", "x": [f"c{i}" for i in range(12)], "y": list(range(12))}],
             "layout": {"title": {"text": "t"}}}]
    warns = chart_qc.qc_figures("Pie_Chart", figs, "<h1>t</h1>")
    assert any("复杂度预算" in w and "类别数 12" in w for w in warns)


def test_qc_line_time_series_not_flagged_as_categories():
    # 365 天的折线是正常形态：类别预算只对类别型 trace 生效
    figs = [{"data": [{"type": "scatter", "mode": "lines", "name": "s",
                       "x": list(range(365)), "y": list(range(365))}],
             "layout": {"title": {"text": "t"}}}]
    warns = chart_qc.qc_figures("Line_Chart", figs)
    assert not any("类别数" in w for w in warns)


def test_qc_bad_labels_and_dupes_and_single_point():
    figs = [{"data": [
        {"type": "scatter", "mode": "lines", "name": "s", "x": [1], "y": [1]},
        {"type": "scatter", "mode": "lines", "name": "s", "x": [1], "y": [2]},
    ], "layout": {"title": {"text": "t"}}}]
    warns = chart_qc.qc_figures("Line_Chart", figs)
    assert any("重复系列名" in w for w in warns)
    assert any("1 个数据点" in w for w in warns)


def test_qc_empty_traces():
    figs = [{"data": [], "layout": {}}]
    warns = chart_qc.qc_figures("Bar_Chart", figs)
    assert any("没有" in w for w in warns)


def test_qc_clean_chart_no_warnings():
    figs = [{"data": [{"type": "bar", "name": "销售", "x": ["a", "b"], "y": [1, 2]}],
             "layout": {"title": {"text": "t"}}}]
    warns = chart_qc.qc_figures("Bar_Chart", figs, "<h1>t</h1>")
    assert warns == []


def test_qc_hidden_component_traces_are_not_series():
    # Ridgeline：密度填充（图例可见）+ 轮廓线（图例隐藏）同名的成对 trace
    # 是一个系列的两个组件，不算重复系列名，也不把 trace 数当系列数
    pairs = []
    for g in ("G0", "G1", "G2", "G3", "G4", "G5"):
        pairs += [
            {"name": g, "type": "scatter", "x": ["a", "b"], "showlegend": True, "hoverinfo": "skip"},
            {"name": g, "type": "scatter", "x": ["a", "b"], "showlegend": False},
        ]
    figs = [{"data": pairs, "layout": {"title": {"text": "t"}}}]
    warns = chart_qc.qc_figures("ridgeline_plot", figs)
    assert not any("重复系列名" in w for w in warns)
    assert not any("系列数" in w for w in warns)


def test_qc_ghost_traces_and_numeric_axis_no_bad_labels():
    # 全 None 的图例代理（Waffle/Horizon）与哑铃连接线的 None 分隔符
    # 都不是 NULL 类别；真实数据中的 None/"" 仍要报
    ghost = {"name": "线上 (50%)", "type": "scatter", "x": [None], "y": [None], "showlegend": True}
    connector = {"type": "scatter", "mode": "lines", "showlegend": False,
                 "x": [80, 70, None, 75, 72, None], "y": ["北京", "北京", None, "上海", "上海", None]}
    figs = [{"data": [ghost, connector], "layout": {"title": {"text": "t"}}}]
    assert chart_qc.qc_figures("dot_plot", figs) == []

    real_bad = {"type": "bar", "x": ["a", None, ""], "y": [1, 2, 3], "showlegend": False}
    warns = chart_qc.qc_figures("bar_chart", [{"data": [real_bad], "layout": {"title": "t"}}])
    assert any("空/NULL 类别" in w and "2 个" in w for w in warns)


def test_qc_parallel_coordinates_row_budget():
    # 平行坐标按分组着色时一组一条图例项；行数是数据量不是系列数
    traces = [{"name": f"车{i}", "type": "scatter", "x": [0, 1, 2], "y": [0, 0, 0],
               "showlegend": True} for i in range(18)]
    figs = [{"data": traces, "layout": {"title": {"text": "t"}}}]
    assert chart_qc.qc_figures("parallel_coordinates_plot", figs) == []


# ── 5. 端到端（真实模块产出） ─────────────────────────────────────────────────

def _make_bar_html(n_categories=5, title="测试柱状图"):
    from charts.Bar_Chart import generate
    df = pd.DataFrame({
        "region": [f"地区{i}" for i in range(n_categories)],
        "sales": list(range(n_categories)),
    })
    result = generate(df=df, mapping={"x": "region", "y": "sales"},
                      options={"title": title})
    assert result.html, result.warnings
    return result.html


def test_end_to_end_bar_chart():
    html = _make_bar_html()
    # Bar_Chart 默认按值降序：sales 最大的是"地区4"（值 4），排第一
    out, figs, warns = postprocess_plotly_html(
        html, "mckinsey", {"highlight": "地区4"}
    )
    assert len(figs) == 1 and not warns
    trace = figs[0]["data"][0]
    roles = semantic_roles("mckinsey")
    colors = trace["marker"]["color"]
    assert isinstance(colors, list)
    assert colors[0] == roles["accent"] and colors[1] == roles["dim"]
    # 主题铬件已进入最终 HTML
    assert roles["rule"] in out
    # 图表功能链路（generate_chart 调度器）也不再丢 warnings
    import chart_generate
    df = pd.DataFrame({"region": [f"地区{i}" for i in range(5)],
                       "sales": [5, 4, 3, 2, 1]})
    res = chart_generate.generate_chart(
        df=df, chart_type="Bar_Chart", mapping={"x": "region", "y": "sales"},
        options={"title": "端到端", "highlight": "地区0"}, color_scheme="mckinsey",
    )
    assert res.get("success"), res
    assert "warnings" in res and res["warnings"] == []
    assert "Plotly.newPlot" in res["html"]


def test_end_to_end_budget_warning_flows_to_result():
    import chart_generate
    df = pd.DataFrame({
        "region": [f"地区{i}" for i in range(25)],
        "sales": list(range(25)),
    })
    res = chart_generate.generate_chart(
        df=df, chart_type="Bar_Chart", mapping={"x": "region", "y": "sales"},
        options={"title": "超预算柱状图"}, color_scheme="mckinsey",
    )
    assert res.get("success"), res
    assert any("复杂度预算" in w for w in res.get("warnings", []))


def test_qc_defers_to_module_category_warning():
    # Pie 模块自己会警告"类别过多"——QC 不重复提示，但模块警告必须透传
    import chart_generate
    df = pd.DataFrame({
        "region": [f"地区{i}" for i in range(12)],
        "sales": list(range(12)),
    })
    res = chart_generate.generate_chart(
        df=df, chart_type="Pie_Chart", mapping={"label": "region", "value": "sales"},
        options={"title": "超预算饼图"}, color_scheme="mckinsey",
    )
    assert res.get("success"), res
    warns = res.get("warnings", [])
    assert any("类别过多" in w for w in warns)
    assert sum("复杂度预算" in w for w in warns) == 0


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
