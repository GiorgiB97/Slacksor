from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


MODEL_AUTO = "auto"
SUPPORTED_MODELS = [
    MODEL_AUTO,
    "gpt-5",
    "claude-sonnet-4.5",
    "gemini-2.5-pro",
]


def normalize_model_name(value: str) -> str:
    return value.strip().lower()


def is_shell_command(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("!") and len(stripped) > 1


def extract_shell_command(text: str) -> str:
    return text.strip()[1:].strip()


SLASH_COMMAND_RE = re.compile(r"(?:^|\s)/([a-zA-Z][\w-]*)")


def extract_slash_commands(text: str) -> list[str]:
    """Find all /command patterns in text. Returns deduplicated command names (without /)."""
    seen: set[str] = set()
    result: list[str] = []
    for match in SLASH_COMMAND_RE.findall(text):
        if match not in seen:
            seen.add(match)
            result.append(match)
    return result


def find_cursor_command(workspace_path: str, command_name: str) -> str | None:
    """Search for a cursor command .md file. Workspace-level takes priority over global."""
    workspace_candidate = Path(workspace_path) / ".cursor" / "commands" / f"{command_name}.md"
    if workspace_candidate.is_file():
        return str(workspace_candidate)
    global_candidate = Path.home() / ".cursor" / "commands" / f"{command_name}.md"
    if global_candidate.is_file():
        return str(global_candidate)
    return None


def build_slash_command_prompt(text: str, workspace_path: str) -> str:
    """Detect /commands in text and append cursor command file references to the prompt."""
    commands = extract_slash_commands(text)
    if not commands:
        return text
    suffixes: list[str] = []
    for cmd in commands:
        path = find_cursor_command(workspace_path, cmd)
        if path:
            suffixes.append(f"Use following cursor command '/{cmd}' ({path})")
    if not suffixes:
        return text
    return text + "\n\n" + "\n".join(suffixes)


def is_ping_command(text: str) -> bool:
    return text.strip().lower() == "ping"


def is_help_command(text: str) -> bool:
    return text.strip().lower() == "help"


def is_branch_command(text: str) -> bool:
    return text.strip().lower() == "branch"


def is_status_command(text: str) -> bool:
    return text.strip().lower() == "status"


def is_diff_command(text: str) -> bool:
    return text.strip().lower() == "diff"


def parse_checkout_command(text: str) -> tuple[bool, str | None]:
    parts = text.split()
    if not parts or parts[0].lower() != "checkout":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2:
        return True, parts[1]
    return False, None


def parse_stash_command(text: str) -> tuple[bool, str | None]:
    parts = text.split()
    if not parts or parts[0].lower() != "stash":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2:
        return True, parts[1]
    return False, None


def is_pull_command(text: str) -> bool:
    return text.strip().lower() == "pull"


def is_ls_command(text: str) -> bool:
    return text.strip().lower() == "ls"


def is_dir_command(text: str) -> bool:
    return text.strip().lower() == "dir"


def is_log_command(text: str) -> bool:
    return text.strip().lower() == "log"


def is_last_command(text: str) -> bool:
    return text.strip().lower() == "last"


def is_whoami_command(text: str) -> bool:
    return text.strip().lower() == "whoami"


def parse_blame_command(text: str) -> tuple[bool, str | None]:
    parts = text.split()
    if not parts or parts[0].lower() != "blame":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2:
        return True, parts[1]
    return False, None


def is_conflicts_command(text: str) -> bool:
    return text.strip().lower() == "conflicts"


def parse_model_command(text: str) -> tuple[bool, str | None]:
    parts = text.split()
    if not parts or parts[0].lower() != "model":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2:
        return True, parts[1]
    return False, None


def validate_or_normalize_model(value: str) -> str:
    normalized = normalize_model_name(value)
    if not normalized:
        raise ValueError("Model value cannot be empty.")
    if normalized == MODEL_AUTO:
        return MODEL_AUTO
    return value.strip()


def model_help_text(current_model: str, model_options: list[str] | None = None) -> str:
    options_list = model_options if model_options else SUPPORTED_MODELS
    options = "\n".join(f"- `{option}`" for option in options_list)
    return (
        f"Current model: `{current_model}`\n"
        f"Available models:\n{options}\n"
        "Set model with: `model <name>`\n"
        "Use `model auto` to return to default automatic selection."
    )


def bridge_help_text(current_model: str) -> str:
    return (
        "*Bridge commands:*\n"
        "- `help`: show this help.\n"
        "- `ping`: check bridge status, uptime, and queue depth.\n"
        "- `model` / `model <name>`: show or set default model.\n"
        "- `stop` / `exit`: stop the active session.\n"
        "- `!<command>`: run a shell command (e.g. `!git status`).\n"
        "- `/<command>`: use a cursor command (e.g. `/review`).\n"
        "\n*Git commands:*\n"
        "- `status`: git status.\n"
        "- `branch`: git branches.\n"
        "- `checkout <name>`: switch branch (creates if missing).\n"
        "- `diff`: changed lines per file.\n"
        "- `log`: last 15 commits (oneline).\n"
        "- `last`: last commit with file stats.\n"
        "- `stash`: show stash list / `stash <n>` to apply.\n"
        "- `pull`: pull current branch from origin.\n"
        "- `blame <file>`: git blame on a file.\n"
        "- `conflicts`: files with merge conflicts.\n"
        "- `whoami`: git user.name and user.email.\n"
        "\n*Workspace commands:*\n"
        "- `ls`: list files (ls -la).\n"
        "- `dir`: show workspace directory path.\n"
        f"\nDefault model: `{current_model}`."
    )


@dataclass(frozen=True)
class BridgeCommandResult:
    handled: bool
    response_text: str | None = None
