#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM API Key 配置管理
支持 DeepSeek、OpenAI、Claude 等多个 LLM 提供商
支持用户自定义 OpenAI SDK 兼容的模型
"""
import os
import json
from pathlib import Path
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict
import os
from pathlib import Path
# 配置文件路径 - 使用相对路径
if os.environ.get("VERCEL"):
    CONFIG_DIR = Path("/tmp/LLM")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
else:
    CONFIG_DIR = Path(__file__).parent

LLM_CONFIG_FILE = CONFIG_DIR / "llm_config.json"

print(f"[LLM] __file__={__file__}")
print(f"[LLM] CONFIG_DIR={CONFIG_DIR}")
print(f"[LLM] LLM_CONFIG_FILE={LLM_CONFIG_FILE.resolve()}")

@dataclass
class LLMConfig:
    """LLM 配置"""
    provider: str
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    enabled: bool = True
    is_custom: bool = False
    context_window: Optional[int] = None    # 上下文窗口（tokens）
    max_output_tokens: Optional[int] = None  # 最大输出（tokens）
    enable_thinking: bool = False            # 启用推理链（DeepSeek-R1 / Claude 3.7+）


class LLMConfigManager:
    """LLM 配置管理器"""

    DEFAULT_CONFIGS = {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "env_var": "DEEPSEEK_API_KEY",
            "is_custom": False,
            "context_window": 64000,
            "max_output_tokens": 8192,
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "env_var": "OPENAI_API_KEY",
            "is_custom": False,
            "context_window": 128000,
            "max_output_tokens": 16384,
        },
        "claude": {
            "base_url": "https://api.anthropic.com",
            "model": "claude-3-5-haiku-20241022",
            "env_var": "ANTHROPIC_API_KEY",
            "is_custom": False,
            "context_window": 200000,
            "max_output_tokens": 8192,
        },
        "astraflow": {
            "base_url": "https://api-us-ca.umodelverse.ai/v1",
            "model": "gpt-4o-mini",
            "env_var": "ASTRAFLOW_API_KEY",
            "is_custom": False,
            "context_window": 128000,
            "max_output_tokens": 16384,
        },
        "astraflow_cn": {
            "base_url": "https://api.modelverse.cn/v1",
            "model": "gpt-4o-mini",
            "env_var": "ASTRAFLOW_CN_API_KEY",
            "is_custom": False,
            "context_window": 128000,
            "max_output_tokens": 16384,
        }
    }

    def __init__(self, load_from_env: bool = False):
        """
        初始化配置管理器
        load_from_env=False: 默认不从环境变量回灌，避免“删了又出现”
        """
        self.configs: Dict[str, LLMConfig] = {}
        self.load_configs(load_from_env=load_from_env)

    def load_configs(self, load_from_env: bool = False):
        """从文件加载配置"""
        self.configs = {}

        if LLM_CONFIG_FILE.exists():
            try:
                print(f"[LLM] loading from: {LLM_CONFIG_FILE.resolve()}, exists={LLM_CONFIG_FILE.exists()}")
                with open(LLM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for provider, config in data.items():
                        self.configs[provider] = LLMConfig(**config)
            except Exception as e:
                print(f"加载配置失败: {e}")

        if load_from_env:
            self._load_from_env()

    def _load_from_env(self):
        """从环境变量加载内置提供商配置（仅在显式开启时使用）"""
        for provider, defaults in self.DEFAULT_CONFIGS.items():
            env_var = defaults["env_var"]
            api_key = os.environ.get(env_var)

            if api_key and provider not in self.configs:
                self.configs[provider] = LLMConfig(
                    provider=provider,
                    api_key=api_key.strip(),
                    base_url=defaults.get("base_url"),
                    model=defaults.get("model"),
                    enabled=True,
                    is_custom=False
                )

    def save_configs(self):
        """保存配置到文件"""
        try:
            print(f"[LLM] saving to: {LLM_CONFIG_FILE.resolve()}")
            data = {
                provider: asdict(config)
                for provider, config in self.configs.items()
            }
            with open(LLM_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False

    def add_custom_model(
        self, name: str, base_url: str, model_name: str, api_key: str,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False,
    ) -> tuple[bool, str]:
        if not name or not name.strip():
            return False, "模型名称不能为空"
        if not base_url or not base_url.strip():
            return False, "API 调用链接不能为空"
        if not model_name or not model_name.strip():
            return False, "模型名称不能为空"
        if not api_key or not api_key.strip():
            return False, "API Key 不能为空"

        provider_id = f"custom_{name.lower().replace(' ', '_')}"
        if provider_id in self.configs:
            return False, f"模型 '{name}' 已存在"

        self.configs[provider_id] = LLMConfig(
            provider=provider_id,
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            model=model_name.strip(),
            enabled=True,
            is_custom=True,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking,
        )

        if self.save_configs():
            return True, f"模型 '{name}' 添加成功"
        else:
            del self.configs[provider_id]
            return False, "保存配置失败"

    def set_config(
        self, provider: str, api_key: str,
        base_url: Optional[str] = None, model: Optional[str] = None,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False,
    ) -> bool:
        """设置内置提供商配置"""
        if provider not in self.DEFAULT_CONFIGS:
            print(f"不支持的提供商: {provider}")
            return False

        if not api_key or not api_key.strip():
            print("API Key 不能为空")
            return False

        defaults = self.DEFAULT_CONFIGS[provider]
        self.configs[provider] = LLMConfig(
            provider=provider,
            api_key=api_key.strip(),
            base_url=(base_url.strip() if base_url else defaults.get("base_url")),
            model=(model.strip() if model else defaults.get("model")),
            enabled=True,
            is_custom=False,
            context_window=context_window if context_window is not None else defaults.get("context_window"),
            max_output_tokens=max_output_tokens if max_output_tokens is not None else defaults.get("max_output_tokens"),
            enable_thinking=enable_thinking,
        )

        # 关键修复：不再写 os.environ，避免进程内“复活”
        # os.environ[defaults["env_var"]] = api_key

        return self.save_configs()

    def clear_builtin_config(self, provider: str) -> tuple[bool, str]:
        """清空内置 provider 配置（删除文件中的配置，并清理进程环境变量）"""
        if provider not in self.DEFAULT_CONFIGS:
            return False, f"不支持的内置提供商: {provider}"

        self.configs.pop(provider, None)

        # 清理当前进程环境变量（即使你现在不写 env，也防历史残留）
        env_var = self.DEFAULT_CONFIGS[provider]["env_var"]
        os.environ.pop(env_var, None)

        if self.save_configs():
            return True, f"内置配置已清空: {provider}"
        return False, "保存配置失败"

    def get_config(self, provider: str) -> Optional[LLMConfig]:
        return self.configs.get(provider)

    def update_custom_model(
        self, provider: str, base_url: str, model_name: str, api_key: str,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False,
    ) -> tuple[bool, str]:
        """更新已有自定义模型配置"""
        if provider not in self.configs:
            return False, f"配置 '{provider}' 不存在"
        cfg = self.configs[provider]
        if not cfg.is_custom:
            return False, "只能编辑自定义模型"
        if not base_url or not base_url.strip():
            return False, "API Base URL 不能为空"
        if not model_name or not model_name.strip():
            return False, "Model ID 不能为空"
        # api_key 留空则保留旧值
        new_key = api_key.strip() if api_key and api_key.strip() else cfg.api_key
        old = self.configs[provider]
        self.configs[provider] = LLMConfig(
            provider=provider,
            api_key=new_key,
            base_url=base_url.strip(),
            model=model_name.strip(),
            enabled=cfg.enabled,
            is_custom=True,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking,
        )
        if self.save_configs():
            return True, f"配置已更新"
        self.configs[provider] = old
        return False, "保存失败"

    def delete_config(self, provider: str) -> tuple[bool, str]:
        """删除配置（仅自定义）"""
        if provider not in self.configs:
            return False, f"配置 '{provider}' 不存在"

        config = self.configs[provider]
        if not config.is_custom:
            return False, f"无法删除内置提供商 '{provider}'，请使用 clear_builtin_config"

        del self.configs[provider]
        if self.save_configs():
            return True, f"配置 '{provider}' 已删除"
        else:
            self.configs[provider] = config
            return False, "删除失败"

    def get_enabled_providers(self) -> List[str]:
        return [p for p, c in self.configs.items() if c.enabled]

    def get_custom_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "provider": provider,
                "name": config.model,
                "base_url": config.base_url,
                "enabled": config.enabled
            }
            for provider, config in self.configs.items()
            if config.is_custom
        ]

    def get_default_provider(self) -> Optional[str]:
        priority = ["deepseek", "openai", "claude", "astraflow", "astraflow_cn"]
        for provider in priority:
            if provider in self.configs and self.configs[provider].enabled:
                return provider

        for provider, config in self.configs.items():
            if config.is_custom and config.enabled:
                return provider

        return None

    def list_configs(self) -> Dict[str, Any]:
        """注意：不返回 api_key 明文"""
        result = {}
        for provider, config in self.configs.items():
            result[provider] = {
                "provider": config.provider,
                "base_url": config.base_url,
                "model": config.model,
                "enabled": config.enabled,
                "is_custom": config.is_custom,
                "has_api_key": bool(config.api_key),
                "context_window": config.context_window,
                "max_output_tokens": config.max_output_tokens,
                "enable_thinking": config.enable_thinking,
            }
        return result

    def test_config(self, provider: str) -> Dict[str, Any]:
        config = self.get_config(provider)
        if not config:
            return {"success": False, "message": f"未找到 {provider} 的配置", "provider": provider}

        try:
            from openai import OpenAI
            client = OpenAI(api_key=config.api_key, base_url=config.base_url)
            client.chat.completions.create(
                model=config.model,
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=10
            )
            return {"success": True, "message": "配置有效", "provider": provider, "model": config.model}
        except Exception as e:
            return {"success": False, "message": f"测试失败: {str(e)}", "provider": provider, "model": config.model}


_config_manager = None


def get_config_manager() -> LLMConfigManager:
    global _config_manager
    if _config_manager is None:
        # 默认禁用 env 回灌
        _config_manager = LLMConfigManager(load_from_env=False)
    return _config_manager


def get_llm_client(provider: Optional[str] = None):
    manager = get_config_manager()

    if provider is None:
        provider = manager.get_default_provider()
    if provider is None:
        raise ValueError("未配置任何 LLM 提供商")

    config = manager.get_config(provider)
    if not config:
        raise ValueError(f"未找到 {provider} 的配置")

    from openai import OpenAI
    return OpenAI(api_key=config.api_key, base_url=config.base_url)


if __name__ == "__main__":
    manager = get_config_manager()
    print("当前配置:")
    print(json.dumps(manager.list_configs(), indent=2, ensure_ascii=False))
    print(f"\n默认提供商: {manager.get_default_provider()}")
