"""Context-aware availability rules for Slash Commands."""
from __future__ import annotations

from dataclasses import dataclass

from .models import CommandDef


@dataclass(frozen=True)
class CommandAvailabilityContext:
    """Small, serializable state snapshot used by availability rules."""

    history_length: int = 0
    model_available: bool = False
    workspace_mounted: bool = False


@dataclass(frozen=True)
class CommandAvailability:
    available: bool = True
    code: str = ""
    reason: str = ""

    def to_public_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "unavailable_code": self.code,
            "unavailable_reason": self.reason,
        }


class CommandAvailabilityProvider:
    """Evaluate trusted command prerequisites without executing a command."""

    _WORKSPACE_COMMANDS = frozenset({"checkpoint", "teams"})

    def evaluate(
        self,
        command: CommandDef,
        context: CommandAvailabilityContext | None,
    ) -> CommandAvailability:
        # A catalog requested without a Session remains a generic catalog.
        if context is None:
            return CommandAvailability()

        if command.name == "compact":
            if context.history_length < 4:
                return CommandAvailability(
                    False,
                    "not_enough_context",
                    "当前对话内容较少，暂时不需要压缩。",
                )
            if not context.model_available:
                return CommandAvailability(
                    False,
                    "model_required",
                    "请先配置并选择模型。",
                )

        if command.name in self._WORKSPACE_COMMANDS and not context.workspace_mounted:
            return CommandAvailability(
                False,
                "workspace_required",
                "请先连接工作目录。",
            )

        return CommandAvailability()


availability_provider = CommandAvailabilityProvider()
