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
import math
from pathlib import Path
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict
from infrastructure.paths import runtime_config_path

log = logging.getLogger(__name__)

LLM_CONFIG_FILE = runtime_config_path("llm_config.json", "LLM/llm_config.json")
CONFIG_DIR = LLM_CONFIG_FILE.parent
DEFAULT_CONTEXT_WINDOW = 1_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 384_000


HOSTED_MODEL_SPECS = (
    {
        "id": "builtin-free-v1",
        "env_prefix": "BAA_HOSTED_FREE",
        "tier": "free",
        "quota_source": "daily_gift",
        "display_name": "免费模型",
        "public_model_name": "MiniMax-M3",
        "description": "适合日常分析与基础任务",
        "quota_multiplier": 1.0,
        "input_multiplier": 1.0,
        "output_multiplier": 1.0,
        "cache_read_multiplier": 1.0,
        "cache_write_multiplier": 1.0,
        "requires_paid_balance": False,
    },
    {
        "id": "builtin-standard-deepseek-flash-v1",
        "env_prefix": "BAA_HOSTED_STANDARD_DEEPSEEK_FLASH",
        "tier": "standard",
        "quota_source": "persistent_balance",
        "display_name": "DeepSeek V4 Flash",
        "public_model_name": "DeepSeek V4 Flash",
        "description": "快速响应，适合日常对话、内容创作与基础分析",
        "quota_multiplier": 0.71,
        "input_multiplier": 0.71,
        "output_multiplier": 2.12,
        "cache_read_multiplier": 0.03,
        "cache_write_multiplier": 2.12,
        "requires_paid_balance": True,
    },
    {
        "id": "builtin-standard-glm-5-2-v1",
        "env_prefix": "BAA_HOSTED_STANDARD_GLM_5_2",
        "tier": "standard",
        "quota_source": "persistent_balance",
        "display_name": "GLM-5.2",
        "public_model_name": "GLM-5.2",
        "description": "长文本与代码能力均衡，适合复杂系统任务",
        "quota_multiplier": 1.89,
        "input_multiplier": 1.89,
        "output_multiplier": 6.59,
        "cache_read_multiplier": 0.48,
        "cache_write_multiplier": 6.59,
        "requires_paid_balance": True,
    },
    {
        "id": "builtin-advanced-glm-5-3-v1",
        "env_prefix": "BAA_HOSTED_ADVANCED_GLM_5_3",
        "tier": "advanced",
        "quota_source": "persistent_balance",
        "display_name": "GLM-5.3",
        "public_model_name": "GLM-5.3",
        "description": "适合长周期编码、智能体工程与高阶推理",
        "quota_multiplier": 1.89,
        "input_multiplier": 1.89,
        "output_multiplier": 6.59,
        "cache_read_multiplier": 0.48,
        "cache_write_multiplier": 6.59,
        "requires_paid_balance": True,
    },
    {
        "id": "builtin-advanced-deepseek-v4-pro-v1",
        "env_prefix": "BAA_HOSTED_ADVANCED_DEEPSEEK_V4_PRO",
        "tier": "advanced",
        "quota_source": "persistent_balance",
        "display_name": "DeepSeek V4 Pro",
        "public_model_name": "DeepSeek V4 Pro",
        "description": "适合专业代码、科研任务与复杂推理",
        "quota_multiplier": 2.12,
        "input_multiplier": 2.12,
        "output_multiplier": 6.36,
        "cache_read_multiplier": 0.08,
        "cache_write_multiplier": 6.36,
        "requires_paid_balance": True,
    },
    {
        "id": "builtin-advanced-kimi-k3-v1",
        "env_prefix": "BAA_HOSTED_ADVANCED_KIMI_K3",
        "tier": "advanced",
        "quota_source": "persistent_balance",
        "display_name": "Kimi-K3",
        "public_model_name": "Kimi-K3",
        "description": "原生视觉与百万上下文，适合长程知识工作",
        "quota_multiplier": 4.71,
        "input_multiplier": 4.71,
        "output_multiplier": 23.53,
        "cache_read_multiplier": 0.48,
        "cache_write_multiplier": 23.53,
        "requires_paid_balance": True,
    },
)


def model_token_limits(config: Any | None) -> tuple[int, int]:
    """Return configured model limits, with generous safe defaults.

    Custom OpenAI-compatible providers do not always declare their limits.
    Treat a missing, zero, or malformed value as the product default instead
    of falling back to legacy small-context assumptions.
    """
    def _positive(value: Any, fallback: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed > 0 else fallback

    return (
        _positive(getattr(config, "context_window", None), DEFAULT_CONTEXT_WINDOW),
        _positive(getattr(config, "max_output_tokens", None), DEFAULT_MAX_OUTPUT_TOKENS),
    )


def auxiliary_token_limits(config: Any | None, ratio: float = 0.8) -> tuple[int, int]:
    """Return context/output allowances for non-interactive LLM helpers.

    The main chat uses 100% of the configured capability.  Background memory
    extraction and prompt suggestions use 80%, leaving a deliberate buffer
    while still benefiting from long-context/reasoning models.
    """
    context_window, max_output_tokens = model_token_limits(config)
    safe_ratio = min(1.0, max(0.01, float(ratio)))
    return (
        max(1, int(context_window * safe_ratio)),
        max(1, int(max_output_tokens * safe_ratio)),
    )

# 本地模型占位 API Key（Ollama 等本地推理服务无需鉴权，但 OpenAI SDK 要求非空）
LOCAL_KEY_PLACEHOLDER = "no-key"


def _is_local_base_url(url: Optional[str]) -> bool:
    """判断 base_url 是否指向本地地址——本地模型无需 API Key。"""
    if not url:
        return False
    u = url.lower()
    local_markers = (
        "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
        "0:0:0:0:0:0:0:1",
    )
    return any(m in u for m in local_markers)


def _normalise_ollama_base_url(url: Optional[str]) -> str:
    """Return Ollama's OpenAI-compatible base URL.

    The application uses the OpenAI SDK, which speaks to Ollama through
    ``/v1``. ``/api/chat`` is Ollama's native API and is a common but
    incompatible value to put in this field, so convert it safely on save.
    """
    value = (url or "").strip().rstrip("/")
    if not value:
        return "http://localhost:11434/v1"
    if value.endswith("/api/chat"):
        return f"{value[:-len('/api/chat')]}/v1"
    if value.endswith("/api"):
        return f"{value[:-len('/api')]}/v1"
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


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
    # Optional USD-per-million-token rates.  They are configuration metadata,
    # never credentials, and power the auditable Workflow cost view.
    input_price_per_million: Optional[float] = None
    output_price_per_million: Optional[float] = None
    # Platform-managed models are loaded from deployment secrets.  They may be
    # selected like any other provider internally, but must never be returned
    # by the user-editable model settings API or persisted to llm_config.json.
    is_hosted: bool = False


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
        "minimax": {
            "base_url": "https://api.minimaxi.com/v1",
            "model": "MiniMax-M3",
            "env_var": "MINIMAX_API_KEY",
            "is_custom": False,
            "context_window": 1000000,
            "max_output_tokens": 384000,
            "enable_thinking": True,
            "thinking_budget": 8000,
            "supports_prompt_cache": None,
            "prompt_cache_mode": None,
            "prompt_cache_retention": "in_memory",
            "input_price_per_million": 5.0,
            "output_price_per_million": 5.0,
        },
        "ollama": {
            # Ollama 提供 OpenAI 兼容端点：http://localhost:11434/v1
            # 本地推理无需 API Key，调用时使用 LOCAL_KEY_PLACEHOLDER 占位
            "base_url": "http://localhost:11434/v1",
            "model": "",
            "env_var": None,  # 本地服务，无环境变量
            "is_custom": False,
            "context_window": 0,
            "max_output_tokens": 0,
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
        self.hosted_models: Dict[str, Dict[str, Any]] = {}
        self.load_configs(load_from_env=load_from_env)

    def load_configs(self, load_from_env: bool = False):
        """从文件加载配置"""
        self.configs = {}
        self.hosted_models = {}

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
            self._load_hosted_models_from_env()

    @staticmethod
    def _env_flag(name: str, default: bool = False) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _load_hosted_models_from_env(self):
        """Load deployment-managed models while keeping upstream data private.

        Stable public IDs and default display metadata live in code. Railway
        controls every upstream URL, real model ID and credential. Only the
        explicit public catalog fields below may cross the browser boundary.
        """
        def _positive_int_env(name: str, fallback: int) -> int:
            try:
                value = int(os.environ.get(name, ""))
            except (TypeError, ValueError):
                return fallback
            return value if value > 0 else fallback

        def _positive_float_env(name: str, fallback: float) -> float:
            try:
                value = float(os.environ.get(name, ""))
            except (TypeError, ValueError):
                return fallback
            return value if math.isfinite(value) and value > 0 else fallback

        for spec in HOSTED_MODEL_SPECS:
            prefix = spec["env_prefix"]
            if not self._env_flag(f"{prefix}_ENABLED"):
                continue

            is_paid = spec["quota_source"] == "persistent_balance"
            shared_paid_base_url = (
                os.environ.get("BAA_HOSTED_PAID_BASE_URL") or ""
            ).strip()
            shared_paid_api_key = (
                os.environ.get("BAA_HOSTED_PAID_API_KEY") or ""
            ).strip()
            base_url = (
                os.environ.get(f"{prefix}_BASE_URL")
                or (shared_paid_base_url if is_paid else "")
            ).strip()
            upstream_model = (os.environ.get(f"{prefix}_MODEL") or "").strip()
            api_key = (
                os.environ.get(f"{prefix}_API_KEY")
                or (shared_paid_api_key if is_paid else "")
            ).strip()
            missing = [
                name for name, value in (
                    (f"{prefix}_BASE_URL", base_url),
                    (f"{prefix}_MODEL", upstream_model),
                    (f"{prefix}_API_KEY", api_key),
                ) if not value
            ]
            if missing:
                log.error(
                    "[hosted-models] %s disabled; missing %s",
                    spec["id"],
                    ", ".join(missing),
                )
                continue

            display_name = (
                os.environ.get(f"{prefix}_DISPLAY_NAME") or spec["display_name"]
            ).strip()
            public_model_name = (
                os.environ.get(f"{prefix}_PUBLIC_MODEL_NAME")
                or spec["public_model_name"]
            ).strip()
            description = (
                os.environ.get(f"{prefix}_DESCRIPTION") or spec["description"]
            ).strip()
            model_id = spec["id"]

            self.configs[model_id] = LLMConfig(
                provider=model_id,
                api_key=api_key,
                base_url=base_url,
                model=upstream_model,
                name=display_name,
                enabled=True,
                is_custom=False,
                context_window=_positive_int_env(
                    f"{prefix}_CONTEXT_WINDOW", DEFAULT_CONTEXT_WINDOW
                ),
                max_output_tokens=_positive_int_env(
                    f"{prefix}_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS
                ),
                enable_thinking=self._env_flag(f"{prefix}_ENABLE_THINKING"),
                thinking_budget=_positive_int_env(f"{prefix}_THINKING_BUDGET", 8000),
                is_hosted=True,
            )
            self.hosted_models[model_id] = {
                "id": model_id,
                "display_name": display_name,
                "public_model_name": public_model_name,
                "tier": spec["tier"],
                "description": description,
                "quota_source": spec["quota_source"],
                "quota_multiplier": _positive_float_env(
                    f"{prefix}_QUOTA_MULTIPLIER", spec["quota_multiplier"]
                ),
                "input_multiplier": _positive_float_env(
                    f"{prefix}_INPUT_MULTIPLIER", spec["input_multiplier"]
                ),
                "output_multiplier": _positive_float_env(
                    f"{prefix}_OUTPUT_MULTIPLIER", spec["output_multiplier"]
                ),
                "cache_read_multiplier": _positive_float_env(
                    f"{prefix}_CACHE_READ_MULTIPLIER",
                    spec["cache_read_multiplier"],
                ),
                "cache_write_multiplier": _positive_float_env(
                    f"{prefix}_CACHE_WRITE_MULTIPLIER",
                    spec["cache_write_multiplier"],
                ),
                "requires_paid_balance": spec["requires_paid_balance"],
                "available": True,
            }

    def sync_remote_hosted_models(
        self,
        models: list[dict[str, Any]],
        *,
        base_url: str,
        api_key: str,
    ) -> None:
        """Register the cloud catalog as in-memory desktop proxy configs.

        The desktop client receives only stable public model IDs and display
        metadata.  Every actual LLM request is sent to the authenticated cloud
        gateway, so upstream provider URLs, real model names and credentials
        never enter the desktop process or browser response.
        """
        endpoint = (base_url or "").strip().rstrip("/")
        token = (api_key or "").strip()
        if not endpoint or not token:
            return

        for provider, config in list(self.configs.items()):
            if config.is_hosted:
                del self.configs[provider]
        self.hosted_models = {}

        specs = {str(spec["id"]): spec for spec in HOSTED_MODEL_SPECS}

        def _weight(value: Any, fallback: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return fallback
            return parsed if math.isfinite(parsed) and parsed >= 0 else fallback

        for item in models if isinstance(models, list) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            spec = specs.get(model_id)
            if not spec or not item.get("available"):
                continue

            display_name = str(
                item.get("display_name") or spec["display_name"]
            ).strip()
            public_model_name = str(
                item.get("public_model_name") or spec["public_model_name"]
            ).strip()
            self.configs[model_id] = LLMConfig(
                provider=model_id,
                api_key=token,
                base_url=endpoint,
                model=model_id,
                name=display_name,
                enabled=True,
                is_custom=False,
                context_window=DEFAULT_CONTEXT_WINDOW,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                is_hosted=True,
            )
            self.hosted_models[model_id] = {
                "id": model_id,
                "display_name": display_name,
                "public_model_name": public_model_name,
                "tier": str(item.get("tier") or spec["tier"]),
                "description": str(
                    item.get("description") or spec["description"]
                ),
                "quota_source": str(
                    item.get("quota_source") or spec["quota_source"]
                ),
                "quota_multiplier": _weight(
                    item.get("quota_multiplier"), spec["quota_multiplier"]
                ),
                "input_multiplier": _weight(
                    item.get("input_multiplier"), spec["input_multiplier"]
                ),
                "output_multiplier": _weight(
                    item.get("output_multiplier"), spec["output_multiplier"]
                ),
                "cache_read_multiplier": _weight(
                    item.get("cache_read_multiplier"),
                    spec["cache_read_multiplier"],
                ),
                "cache_write_multiplier": _weight(
                    item.get("cache_write_multiplier"),
                    spec["cache_write_multiplier"],
                ),
                "requires_paid_balance": bool(
                    item.get("requires_paid_balance", spec["requires_paid_balance"])
                ),
                "available": True,
                "selectable": item.get("selectable") is not False,
            }

    def _load_from_env(self):
        """从环境变量加载内置提供商配置（仅在显式开启时使用）

        支持通过环境变量覆盖每个 provider 的 base_url 和 model：
        - {PROVIDER}_BASE_URL：覆盖默认 base_url
        - {PROVIDER}_MODEL：覆盖默认 model
        例如 MINIMAX_BASE_URL / MINIMAX_MODEL 可自定义 MiniMax 的端点和模型。
        """
        for provider, defaults in self.DEFAULT_CONFIGS.items():
            env_var = defaults.get("env_var")
            if not env_var:
                # ollama 等本地 provider 无 env_var，跳过
                continue
            api_key = os.environ.get(env_var)

            if api_key and provider not in self.configs:
                # 支持通过 {PROVIDER}_BASE_URL / {PROVIDER}_MODEL 覆盖默认值
                base_url = os.environ.get(
                    f"{provider.upper()}_BASE_URL",
                    defaults.get("base_url"),
                )
                model = os.environ.get(
                    f"{provider.upper()}_MODEL",
                    defaults.get("model"),
                )
                self.configs[provider] = LLMConfig(
                    provider=provider,
                    api_key=api_key.strip(),
                    base_url=base_url,
                    model=model,
                    enabled=True,
                    is_custom=False,
                    context_window=defaults.get("context_window"),
                    max_output_tokens=defaults.get("max_output_tokens"),
                    enable_thinking=defaults.get("enable_thinking", False),
                    thinking_budget=defaults.get("thinking_budget", 8000),
                    supports_prompt_cache=defaults.get("supports_prompt_cache"),
                    prompt_cache_mode=defaults.get("prompt_cache_mode"),
                    prompt_cache_retention=defaults.get(
                        "prompt_cache_retention", "in_memory"
                    ),
                    cache_breakpoint_strategy=defaults.get(
                        "cache_breakpoint_strategy", "stable_prefix"
                    ),
                    input_price_per_million=defaults.get("input_price_per_million"),
                    output_price_per_million=defaults.get("output_price_per_million"),
                )

    def save_configs(self):
        """保存配置到文件"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                provider: asdict(config)
                for provider, config in self.configs.items()
                if not config.is_hosted
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
        input_price_per_million: Optional[float] = None,
        output_price_per_million: Optional[float] = None,
        allow_anonymous: bool = False,
    ) -> tuple[bool, str]:
        if not name or not name.strip():
            return False, "模型名称不能为空"
        if not base_url or not base_url.strip():
            return False, "API 调用链接不能为空"
        if not model_name or not model_name.strip():
            return False, "模型名称不能为空"
        # 本地模型（如 Ollama）无需 API Key，自动填占位符
        if not api_key or not api_key.strip():
            if _is_local_base_url(base_url) or allow_anonymous:
                api_key = LOCAL_KEY_PLACEHOLDER
            else:
                return False, "API Key 不能为空"
        else:
            api_key = api_key.strip()

        provider_id = f"custom_{name.lower().replace(' ', '_')}"
        if provider_id in self.configs:
            return False, f"模型 '{name}' 已存在"

        input_price, output_price = self._price_pair(
            input_price_per_million, output_price_per_million,
        )
        self.configs[provider_id] = LLMConfig(
            provider=provider_id,
            api_key=api_key,
            base_url=base_url.strip(),
            model=model_name.strip(),
            name=name.strip(),               # 供应商显示名称（用户填写的 ac-name）
            enabled=True,
            is_custom=True,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
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
        input_price_per_million: Optional[float] = None,
        output_price_per_million: Optional[float] = None,
    ) -> bool:
        """设置内置提供商配置"""
        if provider not in self.DEFAULT_CONFIGS:
            log.warning("不支持的提供商: %s", provider)
            return False

        defaults = self.DEFAULT_CONFIGS[provider]
        effective_base_url = (
            base_url.strip() if base_url and base_url.strip()
            else defaults.get("base_url")
        )
        if provider == "ollama":
            effective_base_url = _normalise_ollama_base_url(effective_base_url)

        # 本地 provider（ollama）无需 API Key，自动填占位符
        if not api_key or not api_key.strip():
            if provider == "ollama" or _is_local_base_url(effective_base_url):
                api_key = LOCAL_KEY_PLACEHOLDER
            else:
                log.warning("API Key 不能为空")
                return False
        else:
            api_key = api_key.strip()

        input_price, output_price = self._price_pair(
            input_price_per_million, output_price_per_million,
        )
        self.configs[provider] = LLMConfig(
            provider=provider,
            api_key=api_key.strip(),
            base_url=effective_base_url,
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
            input_price_per_million=input_price,
            output_price_per_million=output_price,
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
        env_var = self.DEFAULT_CONFIGS[provider].get("env_var")
        if env_var:
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
        input_price_per_million: Optional[float] = None,
        output_price_per_million: Optional[float] = None,
        price_configured: bool = False,
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
        # api_key 留空则保留旧值；本地地址则用占位符
        if api_key and api_key.strip():
            new_key = api_key.strip()
        elif _is_local_base_url(base_url):
            new_key = LOCAL_KEY_PLACEHOLDER
        else:
            new_key = cfg.api_key
        input_price, output_price = (
            self._price_pair(input_price_per_million, output_price_per_million)
            if price_configured else (
                cfg.input_price_per_million, cfg.output_price_per_million,
            )
        )
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
            input_price_per_million=input_price,
            output_price_per_million=output_price,
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
        priority = [spec["id"] for spec in HOSTED_MODEL_SPECS] + [
            "deepseek", "minimax", "openai", "atlascloud", "ollama", "claude",
        ]
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
            if config.is_hosted:
                continue
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
                "input_price_per_million": config.input_price_per_million,
                "output_price_per_million": config.output_price_per_million,
            }
        return result

    def list_hosted_models(self) -> List[Dict[str, Any]]:
        """Return the public hosted catalog; connection details never leave here."""
        return [dict(item) for item in self.hosted_models.values()]

    def get_hosted_model(self, provider: str) -> Optional[Dict[str, Any]]:
        item = self.hosted_models.get(provider)
        return dict(item) if item else None

    def is_hosted_provider(self, provider: Optional[str]) -> bool:
        return bool(provider and provider in self.hosted_models)

    @staticmethod
    def _price_pair(
        input_price_per_million: Optional[float],
        output_price_per_million: Optional[float],
    ) -> tuple[Optional[float], Optional[float]]:
        """Validate optional model pricing as a complete non-negative pair."""
        if input_price_per_million is None and output_price_per_million is None:
            return None, None
        if input_price_per_million is None or output_price_per_million is None:
            raise ValueError("模型价格必须同时填写输入与输出单价")
        try:
            input_price = float(input_price_per_million)
            output_price = float(output_price_per_million)
        except (TypeError, ValueError) as exc:
            raise ValueError("模型价格必须是数字") from exc
        if (
            not math.isfinite(input_price)
            or not math.isfinite(output_price)
            or input_price < 0
            or output_price < 0
        ):
            raise ValueError("模型价格必须是非负的有限数字")
        return input_price, output_price

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

        # 若没有已保存配置，但传入了临时值，则用默认值补全其余字段。
        # Ollama 可以没有 key，因此不能把空 key 视作「没有临时配置」。
        if not config:
            defaults = self.DEFAULT_CONFIGS.get(provider, {})
            if api_key or base_url or model or provider == "ollama":
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

        if provider == "ollama":
            effective_url = _normalise_ollama_base_url(effective_url)

        if not effective_key:
            # 本地模型（如 Ollama）无需 API Key，使用占位符继续测试
            if _is_local_base_url(effective_url):
                effective_key = LOCAL_KEY_PLACEHOLDER
            else:
                return {"success": False, "message": "API Key 不能为空", "provider": provider}

        try:
            from openai import OpenAI, Timeout
            # Explicit connect timeout: the SDK default (5s) is too tight for
            # proxied networks (e.g. TUN-mode VPN adds ~6s to the handshake).
            client = OpenAI(
                api_key=effective_key,
                base_url=effective_url,
                timeout=Timeout(600.0, connect=15.0),
            )
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
        # 云端托管环境（Railway / Vercel）自动从环境变量加载 LLM 配置，
        # 无需手动在 UI 中填写 API Key；本地开发保持 load_from_env=False。
        is_managed = (
            bool(os.environ.get("RAILWAY_PROJECT_ID"))
            or os.environ.get("VERCEL") == "1"
        )
        _config_manager = LLMConfigManager(load_from_env=is_managed)
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

    from openai import OpenAI, Timeout
    # Application-level retry observers account for every hosted-provider
    # attempt. Disable SDK-transparent retries so provider traffic cannot occur
    # outside that audit trail.
    # Explicit connect timeout: the SDK default (5s) is too tight for proxied
    # networks (TUN-mode VPN handshakes take ~6s). Mirrors the cloud gateway
    # timeout in api/models.py (httpx.Timeout(600, connect=15)).
    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        max_retries=0,
        timeout=Timeout(600.0, connect=15.0),
    )


def get_llm_client_with_fallback(preferred_provider: Optional[str] = None):
    """
    Return (client, provider, config) trying preferred_provider first,
    then falling back through enabled providers in priority order.
    Raises ValueError only when all providers are exhausted.
    """
    import logging
    log = logging.getLogger(__name__)

    manager = get_config_manager()
    from openai import OpenAI, Timeout

    # Build candidate list: preferred first, then priority order
    candidates: List[str] = []
    if preferred_provider:
        candidates.append(preferred_provider)

    priority = ["deepseek", "openai", "atlascloud", "ollama", "claude"]
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
            # Same explicit connect timeout as get_llm_client (see above).
            client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=Timeout(600.0, connect=15.0),
            )
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
