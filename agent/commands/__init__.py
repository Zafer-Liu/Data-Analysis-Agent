"""Slash Command contracts and registry."""

from .availability import (
    CommandAvailability, CommandAvailabilityContext,
    CommandAvailabilityProvider, availability_provider,
)
from .catalog import builtin_commands
from .dispatcher import (
    CommandDispatcher, CommandDispatchError, CommandDispatchResult,
    ParsedCommand, parse_slash_command, render_command_prompt,
)
from .loader import CommandDiagnostic, CommandLoader
from .models import CommandDef, CommandType
from .parser import CommandError, parse_command_file
from .registry import CommandRegistry

__all__ = [
    "CommandDef", "CommandRegistry", "CommandType", "CommandLoader",
    "CommandDiagnostic", "CommandError", "CommandDispatcher",
    "CommandDispatchError", "CommandDispatchResult", "ParsedCommand",
    "CommandAvailability", "CommandAvailabilityContext",
    "CommandAvailabilityProvider", "availability_provider",
    "builtin_commands", "parse_command_file", "parse_slash_command",
    "render_command_prompt",
]
