from __future__ import annotations

import re


SLACK_LINK_RE = re.compile(r"<(https?://[^>|]+)(?:\|[^>]+)?>")
SLACK_USER_RE = re.compile(r"<@([A-Z0-9]+)>")
SLACK_CHANNEL_RE = re.compile(r"<#([A-Z0-9]+)\|([^>]+)>")


def is_stop_command(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"stop", "exit"}


def translate_slack_message(text: str, user_lookup: dict[str, str] | None = None) -> str:
    if not text:
        return ""

    translated = text

    def _replace_user(match: re.Match[str]) -> str:
        user_id = match.group(1)
        if user_lookup is None:
            return f"@{user_id}"
        display = user_lookup.get(user_id, user_id)
        return f"@{display}"

    translated = SLACK_USER_RE.sub(_replace_user, translated)
    translated = SLACK_CHANNEL_RE.sub(r"#\2", translated)
    translated = SLACK_LINK_RE.sub(r"\1", translated)

    return translated.strip()
