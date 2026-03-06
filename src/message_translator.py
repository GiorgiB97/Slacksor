from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


SLACK_LINK_RE = re.compile(r"<(https?://[^>|]+)(?:\|[^>]+)?>")
SLACK_USER_RE = re.compile(r"<@([A-Z0-9]+)>")
SLACK_CHANNEL_RE = re.compile(r"<#([A-Z0-9]+)\|([^>]+)>")
SLACK_MESSAGE_URL_RE = re.compile(
    r"https?://[a-zA-Z0-9._\-]+\.slack\.com/archives/([A-Z0-9]+)/p(\d{10,})(?:\?\S+)?"
)


def is_stop_command(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"stop", "exit"}


@dataclass
class SlackMessageRef:
    url: str
    channel_id: str
    message_ts: str
    thread_ts: str | None


def _p_to_ts(p_value: str) -> str:
    """Convert Slack URL p-timestamp to API ts format ('1772824099371809' -> '1772824099.371809')."""
    if len(p_value) > 10:
        return p_value[:10] + "." + p_value[10:]
    return p_value


def extract_slack_message_urls(text: str) -> list[SlackMessageRef]:
    refs: list[SlackMessageRef] = []
    for match in SLACK_MESSAGE_URL_RE.finditer(text):
        url = match.group(0)
        channel_id = match.group(1)
        message_ts = _p_to_ts(match.group(2))

        thread_ts: str | None = None
        if "?" in url:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            thread_values = params.get("thread_ts", [])
            if thread_values:
                thread_ts = thread_values[0]

        refs.append(SlackMessageRef(
            url=url,
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
        ))
    return refs


SMART_QUOTE_MAP = str.maketrans({
    "\u201c": '"',  # left double curly quote
    "\u201d": '"',  # right double curly quote
    "\u2018": "'",  # left single curly quote
    "\u2019": "'",  # right single curly quote
})


def translate_slack_message(text: str, user_lookup: dict[str, str] | None = None) -> str:
    if not text:
        return ""

    translated = text
    translated = translated.translate(SMART_QUOTE_MAP)

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
