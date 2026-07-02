#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM API Key 配置管理
支持 DeepSeek、OpenAI、Claude 等多个 LLM 提供商
支持用户自定义 OpenAI SDK 兼容的模型
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict
from infrastructure.paths import runtime_config_path

log = logging.getLogger(__name__)

LLM_CONFIG_FILE = runtime_config_path("llm_config.json", "LLM/llm_config.json")
CONFIG_DIR = LLM_CONFIG_FILE.parent
DEFAULT_CONTEXT_WINDOW = 1_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 384_000

@dataclass
class LLMConfig:
    """LLM 配置"""
    provider: str
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    name: Optional[str] = None               # 自定义模型的供应商显示名称（如 "DeepSeek"）
    enabled: bool = True
    is_custom: bool = False
    context_window: Optional[int] = None    # 上下文窗口（tokens）
    max_output_tokens: Optional[int] = None  # 最大输出（tokens）
    enable_thinking: bool = False            # 启用推理链（DeepSeek-R1 / Claude 3.7+）
    thinking_budget: int = 8000              # Claude extended thinking budget_tokens
    supports_prompt_cache: Optional[bool] = None
    prompt_cache_mode: Optional[str] = None
    prompt_cache_retention: str = "in_memory"
    cache_breakpoint_strategy: str = "stable_prefix"


class LLMConfigManager:
    """LLM 配置管理器"""

    DEFAULT_CONFIGS = {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "env_var": "DEEPSEEK_API_KEY",
            "is_custom": False,
            "context_window": DEFAULT_CONTEXT_WINDOW,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "supports_prompt_cache": True,
            "prompt_cache_mode": "deepseek",
            "prompt_cache_retention": "in_memory",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "env_var": "OPENAI_API_KEY",
            "is_custom": False,
            "context_window": DEFAULT_CONTEXT_WINDOW,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "supports_prompt_cache": True,
            "prompt_cache_mode": "openai",
            "prompt_cache_retention": "in_memory",
        },
        "atlascloud": {
            "base_url": "https://api.atlascloud.ai/v1",
            "model": "moonshotai/kimi-k2.6",
            "env_var": "ATLASCLOUD_API_KEY",
            "is_custom": False,
            "context_window": DEFAULT_CONTEXT_WINDOW,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "supports_prompt_cache": False,
            "prompt_cache_mode": "none",
            "prompt_cache_retention": "in_memory",
        },
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
                with open(LLM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for provider, config in data.items():
                        self.configs[provider] = LLMConfig(**config)
            except Exception as e:
                log.error("加载配置失败: %s", e)

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
                    is_custom=False,
                    supports_prompt_cache=defaults.get("supports_prompt_cache"),
                    prompt_cache_mode=defaults.get("prompt_cache_mode"),
                    prompt_cache_retention=defaults.get(
                        "prompt_cache_retention", "in_memory"
                    ),
                    cache_breakpoint_strategy=defaults.get(
                        "cache_breakpoint_strategy", "stable_prefix"
                    ),
                )

    def save_configs(self):
        """保存配置到文件"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                provider: asdict(config)
                for provider, config in self.configs.items()
            }
            with open(LLM_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            log.error("保存配置失败: %s", e)
            return False

    def add_custom_model(
        self, name: str, base_url: str, model_name: str, api_key: str,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False, thinking_budget: int = 8000,
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
            name=name.strip(),               # 供应商显示名称（用户填写的 ac-name）
            enabled=True,
            is_custom=True,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
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
        enable_thinking: bool = False, thinking_budget: int = 8000,
    ) -> bool:
        """设置内置提供商配置"""
        if provider not in self.DEFAULT_CONFIGS:
            log.warning("不支持的提供商: %s", provider)
            return False

        if not api_key or not api_key.strip():
            log.warning("API Key 不能为空")
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
            thinking_budget=thinking_budget,
            supports_prompt_cache=defaults.get("supports_prompt_cache"),
            prompt_cache_mode=defaults.get("prompt_cache_mode"),
            prompt_cache_retention=defaults.get(
                "prompt_cache_retention", "in_memory"
            ),
            cache_breakpoint_strategy=defaults.get(
                "cache_breakpoint_strategy", "stable_prefix"
            ),
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
        enable_thinking: bool = False, thinking_budget: int = 8000,
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
            name=cfg.name,                   # 保留原有供应商显示名称
            enabled=cfg.enabled,
            is_custom=True,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
        )
        if self.save_configs():
            return True, "配置已更新"
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
        priority = ["deepseek", "openai", "atlascloud", "claude"]
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
                "name": config.name,          # 自定义模型的供应商显示名称
                "enabled": config.enabled,
                "is_custom": config.is_custom,
                "has_api_key": bool(config.api_key),
                "context_window": config.context_window,
                "max_output_tokens": config.max_output_tokens,
                "enable_thinking": config.enable_thinking,
                "supports_prompt_cache": config.supports_prompt_cache,
                "prompt_cache_mode": config.prompt_cache_mode,
                "prompt_cache_retention": config.prompt_cache_retention,
                "cache_breakpoint_strategy": config.cache_breakpoint_strategy,
            }
        return result

    def test_config(
        self, provider: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """测试 provider 连通性。

        优先使用传入的临时参数（对应前端输入框中尚未保存的值），
        未传入时退回到已保存配置。这样用户可以「先测后存」。
        """
        config = self.get_config(provider)

        # 若没有已保存配置，但传入了临时 key，则用默认值补全其余字段
        if not config:
            defaults = self.DEFAULT_CONFIGS.get(provider, {})
            if api_key:
                # 用传入参数 + 默认值组成临时配置
                effective_key   = api_key
                effective_url   = base_url or defaults.get("base_url")
                effective_model = model    or defaults.get("model")
            else:
                return {"success": False, "message": f"未找到 {provider} 的配置", "provider": provider}
        else:
            # 有已保存配置：临时参数覆盖对应字段
            effective_key   = api_key   or config.api_key
            effective_url   = base_url  or config.base_url
            effective_model = model     or config.model

        if not effective_key:
            return {"success": False, "message": "API Key 不能为空", "provider": provider}

        try:
            from openai import OpenAI
            client = OpenAI(api_key=effective_key, base_url=effective_url)
            client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=10
            )
            return {"success": True, "message": "配置有效", "provider": provider, "model": effective_model}
        except Exception as e:
            return {"success": False, "message": f"测试失败: {str(e)}", "provider": provider, "model": effective_model}


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


def get_llm_client_with_fallback(preferred_provider: Optional[str] = None):
    """
    Return (client, provider, config) trying preferred_provider first,
    then falling back through enabled providers in priority order.
    Raises ValueError only when all providers are exhausted.
    """
    import logging
    log = logging.getLogger(__name__)

    manager = get_config_manager()
    from openai import OpenAI

    # Build candidate list: preferred first, then priority order
    candidates: List[str] = []
    if preferred_provider:
        candidates.append(preferred_provider)

    priority = ["deepseek", "openai", "atlascloud", "claude"]
    for p in priority:
        if p not in candidates and p in manager.configs and manager.configs[p].enabled:
            candidates.append(p)

    # Append any enabled custom models not already listed
    for p, cfg in manager.configs.items():
        if p not in candidates and cfg.is_custom and cfg.enabled:
            candidates.append(p)

    last_exc: Optional[Exception] = None
    for provider in candidates:
        config = manager.get_config(provider)
        if not config or not config.api_key:
            continue
        try:
            client = OpenAI(api_key=config.api_key, base_url=config.base_url)
            # Lightweight probe — just instantiate, don't make a network call
            log.info("[llm] selected provider=%s model=%s", provider, config.model)
            return client, provider, config
        except Exception as exc:
            log.warning("[llm] provider %s unavailable: %s", provider, exc)
            last_exc = exc

    raise ValueError(f"所有 LLM 提供商均不可用。最后错误: {last_exc}")


if __name__ == "__main__":
    manager = get_config_manager()
    print("当前配置:")
    print(json.dumps(manager.list_configs(), indent=2, ensure_ascii=False))
    print(f"\n默认提供商: {manager.get_default_provider()}")
