# -*- coding: utf-8 -*-
"""
批次③测试：尺寸预设 + spec 边车 + PNG 导出辅助。

覆盖：
1. SIZE_PRESETS / apply_size（预设写死宽高，chat/未知值不动作）
2. postprocess_plotly_html 按 options["size"] 应用预设
3. parse_plotly_figures 只读解析（含非 plotly 空返回）
4. plotly_figure_objects 从真实模块产出重构 Figure
5. render_png_bytes 的错误路径（非 plotly / 多子图 / 缺 kaleido）
6. _ChartStore 的 spec 边车读写往返（含坏 JSON 容错）

可直接 pytest 运行，也可 ``python tests/test_size_spec_png.py`` 裸跑。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pandas as pd

_PROJECT = Path(__file__).resolve().parent.parent  # Charts_generation/
_REPO = _PROJECT.parent.parent                      # 仓库根目录
sys.path.insert(0, str(_PROJECT))
sys.path.insert(0, str(_REPO))

from charts.theme import (  # noqa: E402
    SIZE_PRESETS,
    apply_size,
    parse_plotly_figures,
    plotly_figure_objects,
    postprocess_plotly_html,
    render_png_bytes,
)


# ── 1/2. 尺寸预设 ────────────────────────────────────────────────────────────

def test_apply_size_presets():
    fig = {"data": [], "layout": {}}
    apply_size(fig, "slide")
    assert fig["layout"]["width"] == 1280 and fig["layout"]["height"] == 720
    assert fig["layout"]["autosize"] is False
    # chat（响应式）与未知值都不动作
    fig2 = {"data": [], "layout": {"autosize": True}}
    apply_size(fig2, "chat")
    apply_size(fig2, "")
    apply_size(fig2, "bogus")
    apply_size(fig2, None)
    assert fig2["layout"] == {"autosize": True}


def test_postprocess_applies_size():
    data = [{"type": "bar", "x": ["a", "b"], "y": [1, 2]}]
    import json as _json
    d = _json.dumps(data, separators=(",", ":"))
    html = f'<script>Plotly.newPlot("d", {d}, {{}});</script>'
    out, figs, warns = postprocess_plotly_html(html, "mckinsey", {"size": "square"})
    assert not warns and len(figs) == 1
    assert figs[0]["layout"]["width"] == 800 and figs[0]["layout"]["height"] == 800
    assert '"width":800' in out  # 写回 HTML


# ── 3/4. 只读解析与 Figure 重构 ──────────────────────────────────────────────

def test_parse_plotly_figures_readonly():
    import json as _json
    data = [{"type": "bar", "x": ["甲", "乙"], "y": [1, 2], "name": "S"}]
    d = _json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = f'<html>Plotly.newPlot("d", {d}, {{"title": "T"}})</html>'
    figs, warns = parse_plotly_figures(html)
    assert not warns and len(figs) == 1
    assert figs[0]["data"][0]["x"] == ["甲", "乙"]
    assert figs[0]["layout"]["title"] == "T"
    # 非 plotly / 空 HTML
    assert parse_plotly_figures("<script>echarts.init(x)</script>") == ([], [])
    assert parse_plotly_figures("") == ([], [])


def test_plotly_figure_objects_roundtrip():
    from charts.Bar_Chart import generate
    df = pd.DataFrame({"region": ["华东", "华南", "华北"], "sales": [3, 1, 2]})
    result = generate(df=df, mapping={"x": "region", "y": "sales"},
                      options={"title": "重构测试"})
    assert result.html, result.warnings
    # 先走一遍主题后处理，模拟真实落盘形态
    html, _f, _w = postprocess_plotly_html(result.html, "mckinsey", {})
    figs = plotly_figure_objects(html)
    assert len(figs) == 1
    fig = figs[0]
    assert len(fig.data) == 1
    # 模块会按值降序排布，这里验证数据完整保持（集合级）
    assert set(fig.data[0].x) == {"华东", "华南", "华北"}
    assert "重构测试" in (fig.layout.title.text or "")


# ── 5. PNG 错误路径 ──────────────────────────────────────────────────────────

def test_render_png_bytes_rejects_non_plotly():
    try:
        render_png_bytes("<script>echarts.init(dom)</script>")
        raise AssertionError("非 plotly HTML 应抛 ValueError")
    except ValueError as e:
        assert "Plotly" in str(e)


def test_render_png_bytes_rejects_multi_figure():
    import json as _json
    d = _json.dumps([{"type": "scatter", "x": [1], "y": [1]}], separators=(",", ":"))
    # 迷你图表格形态：一张 HTML 里多个 newPlot
    html = f'Plotly.newPlot("a", {d}, {{}}); Plotly.newPlot("b", {d}, {{}});'
    try:
        render_png_bytes(html)
        raise AssertionError("多子图应抛 ValueError")
    except ValueError as e:
        assert "2" in str(e)


def test_render_png_bytes_kaleido_gate():
    if importlib.util.find_spec("kaleido") is not None:
        return  # 已装 kaleido 的环境走不到 ImportError 分支，跳过
    from charts.Bar_Chart import generate
    df = pd.DataFrame({"region": ["华东", "华南"], "sales": [1, 2]})
    result = generate(df=df, mapping={"x": "region", "y": "sales"},
                      options={"title": "png gate"})
    try:
        render_png_bytes(result.html)
        raise AssertionError("缺 kaleido 应抛 ImportError 且带安装提示")
    except ImportError as e:
        assert "kaleido" in str(e)


# ── 6. spec 边车 ────────────────────────────────────────────────────────────

def test_chart_store_spec_roundtrip():
    from api.state import _ChartStore
    with tempfile.TemporaryDirectory() as d:
        store = _ChartStore(Path(d))
        assert store.get_spec("c1") is None          # 无 spec 返回 None
        store["c1"] = "<html>hi</html>"
        store.save_spec("c1", {
            "chart_type": "Bar_Chart", "sql": "SELECT 1",
            "size": "slide", "中文键": "值",
        })
        spec = store.get_spec("c1")
        assert spec["chart_type"] == "Bar_Chart"
        assert spec["size"] == "slide"
        assert spec["中文键"] == "值"                 # ensure_ascii=False 可读落盘
        assert store["c1"] == "<html>hi</html>"       # spec 不影响 HTML 读取
        # 坏 JSON 容错 → None（不让一张坏边车打挂出图链路）
        (store._dir / "c2.spec.json").write_text("{broken", encoding="utf-8")
        assert store.get_spec("c2") is None


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
