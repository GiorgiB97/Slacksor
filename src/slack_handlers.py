from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from db import Database
from message_translator import (
    extract_slack_message_urls,
    is_stop_command,
    translate_slack_message,
)
from session_manager import STOPPED_MESSAGE, SessionManager
from bridge_commands import (
    bridge_help_text,
    build_slash_command_prompt,
    extract_shell_command,
    is_branch_command,
    is_conflicts_command,
    is_diff_command,
    is_dir_command,
    is_help_command,
    is_last_command,
    is_log_command,
    is_ls_command,
    is_ping_command,
    is_pull_command,
    is_shell_command,
    is_status_command,
    is_whoami_command,
    model_help_text,
    parse_blame_command,
    parse_checkout_command,
    parse_model_command,
    parse_stash_command,
    validate_or_normalize_model,
)

SHELL_COMMAND_TIMEOUT_SECONDS = 30
SHELL_OUTPUT_MAX_CHARS = 3500
MAX_URL_REFERENCES = 3


class SlackClientAdapter:
    def __init__(self, web_client: WebClient, logger: Callable[[str], None] | None = None) -> None:
        self._web = web_client
        self._logger = logger
        self._presence_disabled = False

    def post_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self._web.chat_postMessage(channel=channel_id, text=text, thread_ts=thread_ts)

    def add_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        try:
            self._web.reactions_add(channel=channel_id, timestamp=timestamp, name=emoji)
        except SlackApiError as exc:
            error_code = str(exc.response.get("error", ""))
            if error_code == "already_reacted":
                return
            if self._logger is not None:
                self._logger(f"Slack add reaction failed: {error_code}")
            raise

    def remove_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        try:
            self._web.reactions_remove(channel=channel_id, timestamp=timestamp, name=emoji)
        except SlackApiError as exc:
            try:
                error_code = str(exc.response.get("error", ""))
            except Exception:
                # Slack SDK can raise with malformed/non-JSON responses.
                if self._logger is not None:
                    self._logger("Slack remove reaction failed: non_json_response")
                return
            if not error_code:
                if self._logger is not None:
                    self._logger("Slack remove reaction failed: empty_error")
                return
            if error_code in {"no_reaction", "message_not_found"}:
                return
            if self._logger is not None:
                self._logger(f"Slack remove reaction failed: {error_code}")
            return

    def get_thread_replies(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        """Fetch all messages in a thread (parent + replies), sorted by ts. Returns list of dicts with ts, user, bot_id, text."""
        out: list[dict[str, Any]] = []
        cursor = None
        while True:
            response = self._web.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=200,
                cursor=cursor,
            )
            messages = response.get("messages", [])
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                ts = msg.get("ts")
                text = msg.get("text") or ""
                out.append({
                    "ts": str(ts) if ts else "",
                    "user": msg.get("user"),
                    "bot_id": msg.get("bot_id"),
                    "text": str(text),
                })
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        out.sort(key=lambda m: m["ts"])
        return out

    def set_presence(self, status: str) -> None:
        """Set bot presence. status should be 'auto' (online) or 'away'."""
        if self._presence_disabled:
            return
        try:
            self._web.users_setPresence(presence=status)
        except SlackApiError as exc:
            error_code = str(exc.response.get("error", ""))
            if error_code == "missing_scope":
                self._presence_disabled = True
                if self._logger is not None:
                    self._logger(
                        "Bot presence disabled: add the 'users:write' scope "
                        "in your Slack app and reinstall to enable it"
                    )
                return
            if self._logger is not None:
                self._logger(f"Slack set presence failed: {error_code}")

    def ensure_channel(self, channel_name: str) -> str:
        normalized = channel_name.strip().lstrip("#").lower()
        cursor = None
        while True:
            response = self._web.conversations_list(
                types="public_channel",
                exclude_archived=True,
                limit=1000,
                cursor=cursor,
            )
            channels = response.get("channels", [])
            for channel in channels:
                if channel.get("name") == normalized:
                    channel_id = str(channel["id"])
                    self._web.conversations_join(channel=channel_id)
                    return channel_id
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        created = self._web.conversations_create(name=normalized)
        channel_id = str(created["channel"]["id"])
        self._web.conversations_join(channel=channel_id)
        return channel_id


def _format_uptime(seconds: float) -> str:
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


class SlackEventRouter:
    def __init__(
        self,
        db: Database,
        sessions: SessionManager,
        slack_client: SlackClientAdapter,
        logger: Callable[[str], None],
        model_options_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._db = db
        self._sessions = sessions
        self._slack = slack_client
        self._logger = logger
        self._model_options_provider = model_options_provider
        self._model_cache_ttl_seconds = 15 * 60
        self._started_at = time.time()

    def _get_model_options(self) -> list[str] | None:
        cached = self._db.get_model_options_cache()
        if cached:
            return cached
        if self._model_options_provider is None:
            return None
        try:
            options = self._model_options_provider()
            if options:
                self._db.set_model_options_cache(options, ttl_seconds=self._model_cache_ttl_seconds)
                return list(options)
        except Exception as exc:
            self._logger(f"Model options refresh failed: {exc}")
        stale = self._db.get_model_options_cache(include_expired=True)
        if stale:
            return stale
        return None

    def _build_thread_context(
        self, channel_id: str, thread_ts: str, message_ts: str, current_prompt: str
    ) -> str | None:
        """Fetch thread replies before the current message and format as context for the agent."""
        try:
            replies = self._slack.get_thread_replies(channel_id, thread_ts)
        except SlackApiError as exc:
            if self._logger is not None:
                self._logger(f"Failed to fetch thread replies: {exc}")
            return None
        lines: list[str] = []
        for msg in replies:
            ts = msg.get("ts") or ""
            if ts == message_ts:
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            translated = translate_slack_message(text)
            if not translated:
                continue
            if msg.get("bot_id"):
                lines.append(f"Agent: {translated}")
            else:
                lines.append(f"User: {translated}")
        if not lines:
            return None
        return "Context from this Slack thread:\n\n" + "\n\n".join(lines)

    def _resolve_slack_url_references(self, text: str) -> tuple[str, str | None]:
        """Find Slack message URLs in text, fetch their content, and return (context, error).

        Thread URLs (with thread_ts) return the entire thread.
        Message URLs return the linked message and all follow-up messages in the thread.
        At most MAX_URL_REFERENCES unique URLs are allowed.
        """
        refs = extract_slack_message_urls(text)
        if not refs:
            return "", None

        unique_refs = []
        seen: set[str] = set()
        for ref in refs:
            dedup_key = f"{ref.channel_id}:{ref.thread_ts or ref.message_ts}"
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique_refs.append(ref)

        if len(unique_refs) > MAX_URL_REFERENCES:
            return "", (
                f"Too many URL references ({len(unique_refs)}). "
                f"Please use at most {MAX_URL_REFERENCES}."
            )

        blocks: list[str] = []
        for ref in unique_refs:
            try:
                thread_ts = ref.thread_ts or ref.message_ts
                replies = self._slack.get_thread_replies(ref.channel_id, thread_ts)
                if not replies:
                    continue

                if ref.thread_ts is None:
                    replies = [m for m in replies if (m.get("ts") or "") >= ref.message_ts]

                lines: list[str] = []
                for msg in replies:
                    msg_text = (msg.get("text") or "").strip()
                    if not msg_text:
                        continue
                    translated_msg = translate_slack_message(msg_text)
                    if not translated_msg:
                        continue
                    if msg.get("bot_id"):
                        lines.append(f"Agent: {translated_msg}")
                    else:
                        lines.append(f"User: {translated_msg}")

                if lines:
                    block = f"==== REFERENCED CONTEXT : {ref.url} ====\n"
                    block += "\n\n".join(lines)
                    block += "\n==== END OF REFERENCED CONTEXT ===="
                    blocks.append(block)
            except Exception as exc:
                self._logger(f"Failed to resolve referenced URL {ref.url}: {exc}")

        return "\n\n".join(blocks), None

    @staticmethod
    def _extract_voice_transcription(event: dict[str, Any]) -> str:
        files = event.get("files")
        if not isinstance(files, list):
            return ""

        for file_entry in files:
            if not isinstance(file_entry, dict):
                continue
            transcription = file_entry.get("transcription")
            if isinstance(transcription, str) and transcription.strip():
                return transcription.strip()
            if isinstance(transcription, dict):
                text = transcription.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()

            for key in ("transcription_text", "transcript", "preview"):
                candidate = file_entry.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()

        return ""

    @classmethod
    def _extract_prompt_text(cls, event: dict[str, Any]) -> str:
        transcription = cls._extract_voice_transcription(event)
        if transcription:
            return transcription
        return str(event.get("text", ""))

    def _run_shell_command(
        self, command: str, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            output = result.stdout
            if result.stderr:
                if output:
                    output += "\n"
                output += result.stderr
            if not output.strip():
                output = "(no output)"
            if len(output) > SHELL_OUTPUT_MAX_CHARS:
                output = output[:SHELL_OUTPUT_MAX_CHARS] + "\n... (truncated)"
            response = f"```\n$ {command}\n{output}\n```"
            if result.returncode != 0:
                response += f"\nexit code: {result.returncode}"
        except subprocess.TimeoutExpired:
            response = (
                f"Command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS}s:\n"
                f"```\n$ {command}\n```"
            )
        except Exception as exc:
            response = f"Failed to run command: {exc}"
        self._slack.post_message(channel_id, response, thread_ts=thread_ts)

    def handle_message_event(self, event: dict[str, Any]) -> None:
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return
        channel_id = str(event.get("channel", ""))
        text = self._extract_prompt_text(event)
        ts = str(event.get("ts", ""))
        if not channel_id or not text or not ts:
            return
        thread_ts = str(event.get("thread_ts") or ts)
        project = self._db.get_project_by_channel_id(channel_id)
        if project is None:
            return
        workspace_path = str(project["workspace_path"])
        translated = translate_slack_message(text)
        if not translated:
            return

        if is_shell_command(translated):
            shell_cmd = extract_shell_command(translated)
            if shell_cmd:
                self._run_shell_command(shell_cmd, workspace_path, channel_id, thread_ts)
            return

        if is_help_command(translated):
            current_model = self._db.get_default_model()
            self._slack.post_message(channel_id, bridge_help_text(current_model), thread_ts=thread_ts)
            return

        if is_ping_command(translated):
            uptime = _format_uptime(time.time() - self._started_at)
            active_count = len(self._db.list_running_sessions())
            queued = self._sessions.queue_depth()
            current_model = self._db.get_default_model()
            lines = [
                "Pong! Bridge is alive.",
                f"Uptime: {uptime}",
                f"Active sessions: {active_count}",
                f"Queued messages: {queued}",
                f"Default model: `{current_model}`",
            ]
            self._slack.post_message(channel_id, "\n".join(lines), thread_ts=thread_ts)
            return

        if is_branch_command(translated):
            self._run_git_branch(workspace_path, channel_id, thread_ts)
            return

        if is_status_command(translated):
            self._run_shell_command("git status", workspace_path, channel_id, thread_ts)
            return

        if is_diff_command(translated):
            self._run_git_diff(workspace_path, channel_id, thread_ts)
            return

        is_checkout, checkout_branch = parse_checkout_command(translated)
        if is_checkout:
            self._run_checkout(checkout_branch, workspace_path, channel_id, thread_ts)
            return

        is_stash, stash_index = parse_stash_command(translated)
        if is_stash:
            self._run_stash(stash_index, workspace_path, channel_id, thread_ts)
            return

        if is_pull_command(translated):
            self._run_pull(workspace_path, channel_id, thread_ts)
            return

        if is_ls_command(translated):
            self._run_shell_command("ls -la", workspace_path, channel_id, thread_ts)
            return

        if is_dir_command(translated):
            self._slack.post_message(channel_id, f"`{workspace_path}`", thread_ts=thread_ts)
            return

        if is_log_command(translated):
            self._run_shell_command("git log --oneline -15 --no-color", workspace_path, channel_id, thread_ts)
            return

        if is_last_command(translated):
            self._run_shell_command("git log -1 --stat --no-color", workspace_path, channel_id, thread_ts)
            return

        if is_whoami_command(translated):
            self._run_whoami(workspace_path, channel_id, thread_ts)
            return

        is_blame, blame_file = parse_blame_command(translated)
        if is_blame:
            self._run_blame(blame_file, workspace_path, channel_id, thread_ts)
            return

        if is_conflicts_command(translated):
            self._run_conflicts(workspace_path, channel_id, thread_ts)
            return

        is_model, model_value = parse_model_command(translated)
        if is_model:
            if model_value is None:
                current_model = self._db.get_default_model()
                self._slack.post_message(
                    channel_id,
                    model_help_text(current_model, self._get_model_options()),
                    thread_ts=thread_ts,
                )
                return
            try:
                resolved = validate_or_normalize_model(model_value)
            except ValueError as exc:
                self._slack.post_message(channel_id, f"Invalid model command: {exc}", thread_ts=thread_ts)
                return
            self._db.set_default_model(resolved)
            self._slack.post_message(
                channel_id,
                f"Default model updated to `{resolved}`.",
                thread_ts=thread_ts,
            )
            return

        active = self._sessions.get_active_for_workspace(workspace_path)
        if is_stop_command(translated):
            if active is None:
                if self._sessions.stop_workspace_session(workspace_path):
                    self._slack.post_message(channel_id, STOPPED_MESSAGE, thread_ts=thread_ts)
                else:
                    self._slack.post_message(channel_id, "No active agent session to stop.", thread_ts=thread_ts)
                return
            if active.thread_ts != thread_ts:
                return
            if self._sessions.kill_active_for_workspace(workspace_path, request_thread_ts=thread_ts):
                self._slack.post_message(channel_id, STOPPED_MESSAGE, thread_ts=thread_ts)
            return

        self._slack.add_reaction(channel_id, ts, "eyes")
        self._logger(f"Routing message for workspace={workspace_path} thread={thread_ts}")
        enriched_prompt = build_slash_command_prompt(translated, workspace_path)
        referenced_context, ref_error = self._resolve_slack_url_references(translated)
        if ref_error:
            self._slack.post_message(channel_id, ref_error, thread_ts=thread_ts)
            self._slack.remove_reaction(channel_id, ts, "eyes")
            return
        if referenced_context:
            enriched_prompt = referenced_context + "\n\n" + enriched_prompt
        thread_context = self._build_thread_context(channel_id, thread_ts, ts, translated)
        self._sessions.handle_message(
            workspace_path=workspace_path,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=ts,
            prompt=enriched_prompt,
            model_override=(
                str(project["default_model_override"]) if project.get("default_model_override") else None
            ),
            thread_context=thread_context,
        )

    def _run_git_branch(
        self, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        try:
            result = subprocess.run(
                ["git", "branch", "-v", "--no-color"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                error = result.stderr.strip() or "(unknown error)"
                self._slack.post_message(
                    channel_id, f"git branch failed:\n```\n{error}\n```", thread_ts=thread_ts
                )
                return
            if not output:
                self._slack.post_message(
                    channel_id, "No branches found (is this a git repository?)", thread_ts=thread_ts
                )
                return
            repo_name = Path(workspace_path).name
            branch_lines = output.splitlines()
            header = f"+-- {repo_name} --+"
            border_len = len(header) - 2
            footer = "+" + "-" * border_len + "+"
            formatted: list[str] = [header]
            for line in branch_lines:
                marker = ">>>" if line.startswith("*") else "   "
                formatted.append(f"  {marker} {line.strip()}")
            formatted.append(footer)
            response = "```\n" + "\n".join(formatted) + "\n```"
        except subprocess.TimeoutExpired:
            response = f"git branch timed out after {SHELL_COMMAND_TIMEOUT_SECONDS}s."
        except Exception as exc:
            response = f"Failed to run git branch: {exc}"
        self._slack.post_message(channel_id, response, thread_ts=thread_ts)

    def _run_git_diff(
        self, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        try:
            result = subprocess.run(
                ["git", "diff", "--stat", "--no-color"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            staged_result = subprocess.run(
                ["git", "diff", "--cached", "--stat", "--no-color"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or "(unknown error)"
                self._slack.post_message(
                    channel_id, f"git diff failed:\n```\n{error}\n```", thread_ts=thread_ts
                )
                return

            unstaged = result.stdout.strip()
            staged = staged_result.stdout.strip()

            if not unstaged and not staged:
                self._slack.post_message(
                    channel_id, "No changes detected.", thread_ts=thread_ts
                )
                return

            sections: list[str] = []
            if staged:
                sections.append(f"*Staged changes:*\n```\n{staged}\n```")
            if unstaged:
                sections.append(f"*Unstaged changes:*\n```\n{unstaged}\n```")

            response = "\n\n".join(sections)
            if len(response) > SHELL_OUTPUT_MAX_CHARS:
                response = response[:SHELL_OUTPUT_MAX_CHARS] + "\n... (truncated)"
        except subprocess.TimeoutExpired:
            response = f"git diff timed out after {SHELL_COMMAND_TIMEOUT_SECONDS}s."
        except Exception as exc:
            response = f"Failed to run git diff: {exc}"
        self._slack.post_message(channel_id, response, thread_ts=thread_ts)

    def _run_checkout(
        self, branch_name: str | None, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        if not branch_name:
            self._slack.post_message(
                channel_id, "Usage: `checkout <branch-name>`", thread_ts=thread_ts
            )
            return
        try:
            result = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                output = (result.stdout.strip() or result.stderr.strip() or
                          f"Switched to branch `{branch_name}`.")
                self._slack.post_message(channel_id, output, thread_ts=thread_ts)
                return
            result = subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                output = (result.stdout.strip() or result.stderr.strip() or
                          f"Created and switched to branch `{branch_name}`.")
                self._slack.post_message(channel_id, output, thread_ts=thread_ts)
            else:
                error = result.stderr.strip() or "(unknown error)"
                self._slack.post_message(
                    channel_id, f"checkout failed:\n```\n{error}\n```", thread_ts=thread_ts
                )
        except subprocess.TimeoutExpired:
            self._slack.post_message(
                channel_id,
                f"checkout timed out after {SHELL_COMMAND_TIMEOUT_SECONDS}s.",
                thread_ts=thread_ts,
            )
        except Exception as exc:
            self._slack.post_message(
                channel_id, f"Failed to run checkout: {exc}", thread_ts=thread_ts
            )

    def _run_stash(
        self, index: str | None, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        try:
            if index is None:
                result = subprocess.run(
                    ["git", "stash", "list"],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
                )
                output = result.stdout.strip()
                if not output:
                    self._slack.post_message(
                        channel_id, "Stash is empty.", thread_ts=thread_ts
                    )
                    return
                lines = output.splitlines()[:15]
                self._slack.post_message(
                    channel_id, "```\n" + "\n".join(lines) + "\n```", thread_ts=thread_ts
                )
            else:
                ref = f"stash@{{{index}}}"
                result = subprocess.run(
                    ["git", "stash", "apply", ref],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
                )
                if result.returncode == 0:
                    output = result.stdout.strip() or f"Applied {ref}."
                    self._slack.post_message(channel_id, output, thread_ts=thread_ts)
                else:
                    error = result.stderr.strip() or "(unknown error)"
                    self._slack.post_message(
                        channel_id,
                        f"stash apply failed:\n```\n{error}\n```",
                        thread_ts=thread_ts,
                    )
        except subprocess.TimeoutExpired:
            self._slack.post_message(
                channel_id,
                f"stash timed out after {SHELL_COMMAND_TIMEOUT_SECONDS}s.",
                thread_ts=thread_ts,
            )
        except Exception as exc:
            self._slack.post_message(
                channel_id, f"Failed to run stash: {exc}", thread_ts=thread_ts
            )

    def _run_pull(
        self, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        try:
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            current_branch = branch_result.stdout.strip()
            if not current_branch or branch_result.returncode != 0:
                self._slack.post_message(
                    channel_id, "Could not determine current branch.", thread_ts=thread_ts
                )
                return
            self._run_shell_command(
                f"git pull origin {current_branch}", workspace_path, channel_id, thread_ts
            )
        except subprocess.TimeoutExpired:
            self._slack.post_message(
                channel_id,
                f"pull timed out after {SHELL_COMMAND_TIMEOUT_SECONDS}s.",
                thread_ts=thread_ts,
            )
        except Exception as exc:
            self._slack.post_message(
                channel_id, f"Failed to run pull: {exc}", thread_ts=thread_ts
            )

    def _run_whoami(
        self, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        try:
            name_result = subprocess.run(
                ["git", "config", "user.name"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            email_result = subprocess.run(
                ["git", "config", "user.email"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            name = name_result.stdout.strip() or "(not set)"
            email = email_result.stdout.strip() or "(not set)"
            self._slack.post_message(
                channel_id, f"{name} <{email}>", thread_ts=thread_ts
            )
        except Exception as exc:
            self._slack.post_message(
                channel_id, f"Failed to get git identity: {exc}", thread_ts=thread_ts
            )

    def _run_blame(
        self, file_path: str | None, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        if not file_path:
            self._slack.post_message(
                channel_id, "Usage: `blame <file-path>`", thread_ts=thread_ts
            )
            return
        self._run_shell_command(
            f"git blame --no-color {file_path}", workspace_path, channel_id, thread_ts
        )

    def _run_conflicts(
        self, workspace_path: str, channel_id: str, thread_ts: str
    ) -> None:
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
            output = result.stdout.strip()
            if not output:
                self._slack.post_message(
                    channel_id, "No merge conflicts detected.", thread_ts=thread_ts
                )
                return
            self._slack.post_message(
                channel_id,
                f"*Files with conflicts:*\n```\n{output}\n```",
                thread_ts=thread_ts,
            )
        except Exception as exc:
            self._slack.post_message(
                channel_id, f"Failed to check conflicts: {exc}", thread_ts=thread_ts
            )

    def safe_post(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        try:
            self._slack.post_message(channel_id, text, thread_ts=thread_ts)
        except SlackApiError as exc:
            self._logger(f"Slack post failed: {exc}")
