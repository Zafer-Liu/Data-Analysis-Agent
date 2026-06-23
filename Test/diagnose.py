#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断脚本 — 验证当前架构各模块可正常导入"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "Function" / "Charts_generation"))

print("=" * 60)
print("Business Analytics Agent — 模块诊断")
print("=" * 60)

# 1. chart_selector（图表注册表 + 选图引擎）
print("\n1. 测试 chart_selector 导入...")
try:
    from LLM.chart_selector import _CHARTS, select_charts, format_selection_result
    print("   ✓ chart_selector 导入成功")
    print(f"   - 内嵌图表数量: {len(_CHARTS)}")
    print(f"   - 第一个图表: {_CHARTS[0]['chart_id']}")
    r = select_charts("各月销售额趋势", ["month", "revenue"], top_n=1)
    print(f"   - select_charts 测试: {r[0]['chart_id']} (score={r[0]['_score']})")
except Exception as e:
    print(f"   ✗ chart_selector 失败: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 2. llm_config_manager
print("\n2. 测试 llm_config_manager 导入...")
try:
    from LLM.llm_config_manager import LLMConfigManager
    manager = LLMConfigManager()
    print("   ✓ LLMConfigManager 导入成功")
    print(f"   - 已配置提供商: {manager.get_enabled_providers()}")
except Exception as e:
    print(f"   ✗ llm_config_manager 失败: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 3. agent prompts（_CHART_IDS 来源验证）
print("\n3. 测试 agent.prompts 导入...")
try:
    from agent.prompts import _CHART_IDS, _ANALYZE_GUIDE, get_system_prompt
    ids = _CHART_IDS.split(", ")
    print("   ✓ agent.prompts 导入成功")
    print(f"   - _CHART_IDS 图表数量: {len(ids)}")
    prompt = get_system_prompt()
    print(f"   - system prompt 字符数: {len(prompt)}")
    print(f"   - 含 select_chart 流程: {'select_chart' in prompt}")
except Exception as e:
    print(f"   ✗ agent.prompts 失败: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 4. agent tools_schema
print("\n4. 测试 agent.tools.schemas 导入...")
try:
    from agent.tools.schemas import AGENT_TOOLS
    names = [t["function"]["name"] for t in AGENT_TOOLS]
    print(f"   ✓ AGENT_TOOLS 导入成功，共 {len(names)} 个工具")
    print(f"   - select_chart 在 generate_chart 之前: "
          f"{names.index('select_chart') < names.index('generate_chart')}")
except Exception as e:
    print(f"   ✗ agent.tools.schemas 失败: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 5. chart_generate 入口
print("\n5. 测试 chart_generate 导入...")
try:
    from chart_generate import generate_chart
    print("   ✓ chart_generate 导入成功")
except Exception as e:
    print(f"   ✗ chart_generate 失败: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 6. Flask app
print("\n6. 测试 Flask app 导入...")
try:
    from api import create_app
    app = create_app()
    print("   ✓ Flask app 创建成功")
except Exception as e:
    print(f"   ✗ Flask app 失败: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("✓ 所有模块诊断通过")
print("=" * 60)
