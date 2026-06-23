# scripts/test_smoke_all.py
"""
全局 smoke test：验证所有图表可以导入并生成有效HTML
优先使用各图表自身的 example.xlsx，fallback到通用测试数据
Usage: python test_smoke_all.py
"""
import sys, os
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

import pandas as pd

# 通用测试 DataFrame（fallback用）
GENERIC_DF = pd.DataFrame({
    "x": ["A", "B", "C"],
    "y": [10, 20, 30],
    "source": ["北京", "上海", "广州"],
    "target": ["东京", "纽约", "伦敦"],
    "value": [100, 200, 150],
})

# 各图表专用测试数据（覆盖特殊列类型要求）
CHART_DATA = {
    # 需要数值 actual/target 的图表
    "bullet_chart": pd.DataFrame({
        "label": ["A", "B", "C"],
        "actual": [80, 95, 70],
        "target": [100, 100, 100],
        "range": [60, 80, 90],
    }),
    # 需要经纬度的图表
    "bubble_map": pd.DataFrame({
        "lat": [39.9, 31.2, 23.1],
        "lon": [116.4, 121.4, 113.2],
        "size": [500, 300, 200],
        "city": ["北京", "上海", "广州"],
    }),
    "choropleth_map": pd.DataFrame({
        "location": ["北京", "上海", "广东"],
        "value": [5000, 3000, 4000],
    }),
    "flow_map": pd.DataFrame({
        "source": ["北京", "上海"],
        "target": ["东京", "纽约"],
        "lat": [39.9, 31.2],
        "lon": [116.4, 121.4],
        "flow": [100, 200],
    }),
    "dot_density_map": pd.DataFrame({
        "lat": [39.9, 31.2, 23.1],
        "lon": [116.4, 121.4, 113.2],
        "value": [50, 30, 20],
    }),
    "proportional_symbol": pd.DataFrame({
        "lat": [39.9, 31.2],
        "lon": [116.4, 121.4],
        "value": [500, 300],
        "label": ["北京", "上海"],
    }),
    # voronoi 需要纯数值坐标
    "voronoi": pd.DataFrame({
        "x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "y": [1.0, 1.5, 2.0, 1.2, 1.8, 2.5],
        "value": [10, 20, 15, 25, 30, 10],
        "label": ["A", "B", "C", "D", "E", "F"],
    }),
    # slope_chart 需要数值 start/end
    "slope_chart": pd.DataFrame({
        "start": [20, 30, 40],
        "end": [35, 25, 55],
        "group": ["A组", "B组", "C组"],
    }),
    # pyramid_chart 需要 male/female
    "pyramid_chart": pd.DataFrame({
        "age": ["0-10", "10-20", "20-30"],
        "male": [50, 80, 120],
        "female": [45, 85, 110],
    }),
    # bar_chart 需要 x/y
    "bar_chart": pd.DataFrame({
        "x": ["A", "B", "C"],
        "y": [10, 20, 30],
    }),
    # bump_chart 需要 x/group/y
    "bump_chart": pd.DataFrame({
        "x": [1, 2, 3, 1, 2, 3],
        "group": ["A", "A", "A", "B", "B", "B"],
        "y": [10, 20, 30, 15, 25, 35],
    }),
    # chord_diagram 需要 source/target
    "chord_diagram": pd.DataFrame({
        "source": ["北京", "上海", "广州"],
        "target": ["东京", "纽约", "伦敦"],
        "value": [100, 200, 150],
    }),
    # cycle_chart 需要 phase/value
    "cycle_chart": pd.DataFrame({
        "phase": ["1月", "2月", "3月", "4月", "5月", "6月"],
        "value": [10, 20, 15, 25, 30, 22],
    }),
    # diverging_bar 需要 item/value
    "diverging_bar": pd.DataFrame({
        "item": ["A", "B", "C"],
        "value": [10, -20, 30],
    }),
    # dot_plot 需要 y/value
    "dot_plot": pd.DataFrame({
        "y": ["A", "B", "C"],
        "value": [10, 20, 30],
    }),
    # grouped_bar 需要 x/group/y
    "grouped_bar": pd.DataFrame({
        "x": ["A", "B", "C", "A", "B", "C"],
        "group": ["2023", "2023", "2023", "2024", "2024", "2024"],
        "y": [10, 20, 30, 15, 25, 35],
    }),
    # heatmap 需要 row/col/value
    "heatmap": pd.DataFrame({
        "row": ["A", "A", "B", "B"],
        "col": ["X", "Y", "X", "Y"],
        "value": [10, 20, 30, 40],
    }),
    # horizon_chart 需要 x/value
    "horizon_chart": pd.DataFrame({
        "x": ["1月", "2月", "3月", "4月", "5月"],
        "value": [10, 20, 15, 25, 30],
    }),
    # marimekko 需要 x/y/value
    "marimekko": pd.DataFrame({
        "x": ["A", "A", "B", "B"],
        "y": ["X", "Y", "X", "Y"],
        "value": [10, 20, 30, 40],
    }),
    # scatter_plot 需要 x/y
    "scatter_plot": pd.DataFrame({
        "x": [1, 2, 3, 4, 5],
        "y": [2, 4, 5, 4, 6],
    }),
    # stacked_bar 需要 x/group/y
    "stacked_bar": pd.DataFrame({
        "x": ["A", "B", "C", "A", "B", "C"],
        "group": ["2023", "2023", "2023", "2024", "2024", "2024"],
        "y": [10, 20, 30, 15, 25, 35],
    }),
    # treemap 需要 path/value
    "treemap": pd.DataFrame({
        "path": ["A", "A-X", "A-Y", "B", "B-X"],
        "value": [100, 40, 60, 80, 50],
    }),
    # word_tree
    "word_tree": pd.DataFrame({
        "text": ["hello world", "hello there", "world peace"],
    }),
    # wordcloud 需要 word/frequency
    "wordcloud": pd.DataFrame({
        "word": ["python", "data", "visualization", "chart", "analysis"],
        "frequency": [100, 80, 60, 50, 40],
    }),
}


def get_test_df(chart_id: str) -> pd.DataFrame:
    """获取测试数据：优先用 example.xlsx，再用专用数据，最后用通用数据"""
    # 1. 尝试加载 example.xlsx
    chart_dir = ROOT / "charts" / chart_id
    for fname in ["example.xlsx", "demo.xlsx", "samples/demo.xlsx"]:
        fpath = chart_dir / fname
        if fpath.exists():
            try:
                return pd.read_excel(str(fpath))
            except Exception as e:
                print(f"[WARN] read_excel failed: {fpath} -> {type(e).__name__}: {e}")
    # 2. 专用数据
    if chart_id in CHART_DATA:
        return CHART_DATA[chart_id]
    # 3. 通用数据
    return GENERIC_DF


def test_import(chart_id: str) -> dict:
    """测试单个图表的导入"""
    result = {"chart_id": chart_id, "status": "pending", "msg": ""}
    try:
        mod = __import__(f"charts.{chart_id}.chart", fromlist=["generate"])
        gen = getattr(mod, "generate", None)
        if gen is None:
            result["status"] = "error"
            result["msg"] = "no generate()"
            return result

        # 获取测试数据
        test_df = get_test_df(chart_id)

        # 尝试调用
        try:
            ret = gen(df=test_df.copy())

            if hasattr(ret, "is_valid"):
                html_len = len(ret.html) if getattr(ret, "html", None) else 0
                ok_flag = ret.is_valid()
                result["status"] = "ok" if ok_flag else "error"

                # 关键：把 warnings/meta 摘要写进 msg，方便你立刻知道原因
                warns = getattr(ret, "warnings", None) or []
                meta = getattr(ret, "meta", None) or {}
                wmsg = "; ".join(str(w) for w in warns[:3])  # 只取前3条
                result["msg"] = f"html={html_len}" + (f" | warnings: {wmsg}" if wmsg else "")
                if not ok_flag and meta:
                    # 可选：也打印一点 meta
                    result["msg"] += f" | meta_keys={list(meta.keys())[:6]}"
            elif isinstance(ret, dict):
                result["status"] = "ok" if ret.get("html") else "error"
                result["msg"] = f"html={len(ret.get('html', ''))}"
            elif isinstance(ret, str):
                result["status"] = "ok" if len(ret) > 500 else "error"
                result["msg"] = f"html={len(ret)}"
            else:
                result["status"] = "ok"
                result["msg"] = type(ret).__name__
        except TypeError as e:
            result["status"] = "old_interface"
            result["msg"] = str(e)[:80]
        except Exception as e:
            result["status"] = "runtime_error"
            result["msg"] = f"{type(e).__name__}: {e}"[:80]
    except ImportError as e:
        result["status"] = "import_error"
        result["msg"] = str(e)[:80]
    except Exception as e:
        result["status"] = "error"
        result["msg"] = f"{type(e).__name__}: {e}"[:80]
    return result


def main():
    charts_dir = ROOT / "charts"
    chart_ids = sorted([
        d.name for d in charts_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
        and (d / "chart.py").exists()
    ])

    print(f"\n{'='*60}")
    print(f"Chart Smoke Test | {len(chart_ids)} charts")
    print(f"{'='*60}")

    ok = err = old_int = imp_err = run_err = skip = 0
    results = []

    for cid in chart_ids:
        r = test_import(cid)
        results.append(r)
        icon = {"ok": "[OK]", "error": "[ERR]", "old_interface": "[OLD]", "import_error": "[IMP]",
                "runtime_error": "[RUN]", "pending": "[?]"}.get(r["status"], "[UNK]")
        print(f"  {icon} {cid:<25s} {r['msg']}")

        s = r["status"]
        if s == "ok": ok += 1
        elif s == "error": err += 1
        elif s == "old_interface": old_int += 1
        elif s == "import_error": imp_err += 1
        elif s == "runtime_error": run_err += 1
        else: skip += 1

    print(f"\nResults: ok={ok}  old_interface={old_int}  error={err}  import_error={imp_err}  runtime_error={run_err}")
    if old_int:
        print(f"Note: {old_int} charts need new interface adaptation")
    if err or imp_err:
        print(f"Need fix: {[r['chart_id'] for r in results if r['status'] in ('error','import_error')]}")

    # 保存报告
    import json
    report_path = ROOT / "artifacts" / "smoke_test_report.json"
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"summary": dict(ok=ok, old_interface=old_int, error=err,
                           import_error=imp_err, runtime_error=run_err),
                    "results": results}, f, ensure_ascii=False, indent=2)
    print(f"Report: {report_path}")
    return ok, err + imp_err


if __name__ == "__main__":
    main()
