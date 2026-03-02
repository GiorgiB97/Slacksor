from __future__ import annotations

from pathlib import Path

from bridge_commands import (
    build_slash_command_prompt,
    extract_shell_command,
    extract_slash_commands,
    find_cursor_command,
    is_branch_command,
    is_diff_command,
    is_help_command,
    is_ping_command,
    is_shell_command,
    is_status_command,
    parse_model_command,
    bridge_help_text,
)


def test_is_shell_command_with_bang_prefix() -> None:
    assert is_shell_command("!git status") is True
    assert is_shell_command("!ls -la") is True
    assert is_shell_command("  !echo hello  ") is True


def test_is_shell_command_bare_bang() -> None:
    assert is_shell_command("!") is False
    assert is_shell_command("  !  ") is False


def test_is_shell_command_normal_text() -> None:
    assert is_shell_command("hello") is False
    assert is_shell_command("git status") is False
    assert is_shell_command("") is False


def test_extract_shell_command() -> None:
    assert extract_shell_command("!git status") == "git status"
    assert extract_shell_command("!ls -la") == "ls -la"
    assert extract_shell_command("  !  echo hello  ") == "echo hello"
    assert extract_shell_command("!git log --oneline -5") == "git log --oneline -5"


def test_is_help_command() -> None:
    assert is_help_command("help") is True
    assert is_help_command("/help") is True
    assert is_help_command("!help") is False


def test_parse_model_command_does_not_match_bang() -> None:
    is_model, _ = parse_model_command("!model")
    assert is_model is False


def test_bridge_help_text_mentions_shell_commands() -> None:
    text = bridge_help_text("auto")
    assert "!<command>" in text
    assert "!git status" in text


def test_bridge_help_text_mentions_slash_commands() -> None:
    text = bridge_help_text("auto")
    assert "/<command>" in text
    assert "/review" in text


def test_extract_slash_commands_single() -> None:
    assert extract_slash_commands("/review") == ["review"]


def test_extract_slash_commands_multiple() -> None:
    result = extract_slash_commands("/review fix the bug then /tests")
    assert result == ["review", "tests"]


def test_extract_slash_commands_deduplicates() -> None:
    result = extract_slash_commands("/review code /review again")
    assert result == ["review"]


def test_extract_slash_commands_mid_sentence() -> None:
    assert extract_slash_commands("please run /clean now") == ["clean"]


def test_extract_slash_commands_ignores_urls() -> None:
    assert extract_slash_commands("check https://example.com/path") == []


def test_extract_slash_commands_no_match() -> None:
    assert extract_slash_commands("nothing here") == []
    assert extract_slash_commands("") == []


def test_extract_slash_commands_ignores_bare_slash() -> None:
    assert extract_slash_commands("/ ") == []
    assert extract_slash_commands("/") == []


def test_extract_slash_commands_with_hyphens() -> None:
    assert extract_slash_commands("/branch-review") == ["branch-review"]


def test_find_cursor_command_workspace_level(tmp_path: Path) -> None:
    cmd_dir = tmp_path / ".cursor" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "review.md").write_text("# review command")
    result = find_cursor_command(str(tmp_path), "review")
    assert result == str(cmd_dir / "review.md")


def test_find_cursor_command_not_found(tmp_path: Path) -> None:
    result = find_cursor_command(str(tmp_path), "nonexistent")
    assert result is None


def test_find_cursor_command_workspace_takes_priority_over_global(tmp_path: Path, monkeypatch) -> None:
    workspace_cmd_dir = tmp_path / ".cursor" / "commands"
    workspace_cmd_dir.mkdir(parents=True)
    (workspace_cmd_dir / "review.md").write_text("workspace version")
    global_cmd_dir = tmp_path / "fakehome" / ".cursor" / "commands"
    global_cmd_dir.mkdir(parents=True)
    (global_cmd_dir / "review.md").write_text("global version")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    result = find_cursor_command(str(tmp_path), "review")
    assert result == str(workspace_cmd_dir / "review.md")


def test_find_cursor_command_falls_back_to_global(tmp_path: Path, monkeypatch) -> None:
    global_cmd_dir = tmp_path / "fakehome" / ".cursor" / "commands"
    global_cmd_dir.mkdir(parents=True)
    (global_cmd_dir / "tests.md").write_text("global tests command")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    result = find_cursor_command(str(tmp_path), "tests")
    assert result == str(global_cmd_dir / "tests.md")


def test_build_slash_command_prompt_appends_found_commands(tmp_path: Path) -> None:
    cmd_dir = tmp_path / ".cursor" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "review.md").write_text("# review")
    result = build_slash_command_prompt("/review my code", str(tmp_path))
    assert result.startswith("/review my code\n\n")
    assert f"Use following cursor command '/review' ({cmd_dir / 'review.md'})" in result


def test_build_slash_command_prompt_no_match_returns_original(tmp_path: Path) -> None:
    result = build_slash_command_prompt("just a normal message", str(tmp_path))
    assert result == "just a normal message"


def test_build_slash_command_prompt_command_not_found_returns_original(tmp_path: Path) -> None:
    result = build_slash_command_prompt("/nonexistent do something", str(tmp_path))
    assert result == "/nonexistent do something"


def test_build_slash_command_prompt_multiple_commands(tmp_path: Path) -> None:
    cmd_dir = tmp_path / ".cursor" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "review.md").write_text("# review")
    (cmd_dir / "tests.md").write_text("# tests")
    result = build_slash_command_prompt("/review then /tests", str(tmp_path))
    assert "Use following cursor command '/review'" in result
    assert "Use following cursor command '/tests'" in result


def test_build_slash_command_prompt_partial_match(tmp_path: Path) -> None:
    cmd_dir = tmp_path / ".cursor" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "review.md").write_text("# review")
    result = build_slash_command_prompt("/review and /nonexistent", str(tmp_path))
    assert "Use following cursor command '/review'" in result
    assert "nonexistent" not in result.split("\n\n", 1)[1]


def test_is_ping_command() -> None:
    assert is_ping_command("ping") is True
    assert is_ping_command("/ping") is True
    assert is_ping_command("  ping  ") is True
    assert is_ping_command("PING") is True
    assert is_ping_command("pong") is False
    assert is_ping_command("!ping") is False
    assert is_ping_command("") is False


def test_bridge_help_text_mentions_ping() -> None:
    text = bridge_help_text("auto")
    assert "ping" in text


def test_is_branch_command() -> None:
    assert is_branch_command("branch") is True
    assert is_branch_command("/branch") is True
    assert is_branch_command("  branch  ") is True
    assert is_branch_command("BRANCH") is True
    assert is_branch_command("branches") is False
    assert is_branch_command("") is False


def test_is_status_command() -> None:
    assert is_status_command("status") is True
    assert is_status_command("/status") is True
    assert is_status_command("  status  ") is True
    assert is_status_command("STATUS") is True
    assert is_status_command("git status") is False
    assert is_status_command("") is False


def test_bridge_help_text_mentions_branch() -> None:
    text = bridge_help_text("auto")
    assert "branch" in text


def test_bridge_help_text_mentions_status() -> None:
    text = bridge_help_text("auto")
    assert "status" in text


def test_is_diff_command() -> None:
    assert is_diff_command("diff") is True
    assert is_diff_command("/diff") is False
    assert is_diff_command("  diff  ") is True
    assert is_diff_command("DIFF") is True
    assert is_diff_command("diffs") is False
    assert is_diff_command("git diff") is False
    assert is_diff_command("") is False


def test_bridge_help_text_mentions_diff() -> None:
    text = bridge_help_text("auto")
    assert "diff" in text
