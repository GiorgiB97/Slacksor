from __future__ import annotations

from pathlib import Path

from bridge_commands import (
    build_slash_command_prompt,
    extract_shell_command,
    extract_slash_commands,
    find_cursor_command,
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
    parse_blame_command,
    parse_checkout_command,
    parse_model_command,
    parse_stash_command,
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
    assert is_help_command("/help") is False
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
    assert is_ping_command("/ping") is False
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
    assert is_branch_command("/branch") is False
    assert is_branch_command("  branch  ") is True
    assert is_branch_command("BRANCH") is True
    assert is_branch_command("branches") is False
    assert is_branch_command("") is False


def test_is_status_command() -> None:
    assert is_status_command("status") is True
    assert is_status_command("/status") is False
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


def test_parse_checkout_command_bare() -> None:
    is_cmd, val = parse_checkout_command("checkout")
    assert is_cmd is True
    assert val is None


def test_parse_checkout_command_with_branch() -> None:
    is_cmd, val = parse_checkout_command("checkout feature/foo")
    assert is_cmd is True
    assert val == "feature/foo"


def test_parse_checkout_command_slash_variant_not_matched() -> None:
    is_cmd, _ = parse_checkout_command("/checkout main")
    assert is_cmd is False


def test_parse_checkout_command_no_match() -> None:
    is_cmd, _ = parse_checkout_command("check out something")
    assert is_cmd is False


def test_parse_checkout_command_preserves_case() -> None:
    is_cmd, val = parse_checkout_command("checkout Feature/Bar")
    assert is_cmd is True
    assert val == "Feature/Bar"


def test_parse_stash_command_bare() -> None:
    is_cmd, val = parse_stash_command("stash")
    assert is_cmd is True
    assert val is None


def test_parse_stash_command_with_index() -> None:
    is_cmd, val = parse_stash_command("stash 2")
    assert is_cmd is True
    assert val == "2"


def test_parse_stash_command_slash_variant_not_matched() -> None:
    is_cmd, _ = parse_stash_command("/stash 0")
    assert is_cmd is False


def test_parse_stash_command_no_match() -> None:
    is_cmd, _ = parse_stash_command("stashing things")
    assert is_cmd is False


def test_is_pull_command() -> None:
    assert is_pull_command("pull") is True
    assert is_pull_command("/pull") is False
    assert is_pull_command("  pull  ") is True
    assert is_pull_command("PULL") is True
    assert is_pull_command("pulling") is False
    assert is_pull_command("") is False


def test_is_ls_command() -> None:
    assert is_ls_command("ls") is True
    assert is_ls_command("/ls") is False
    assert is_ls_command("  ls  ") is True
    assert is_ls_command("LS") is True
    assert is_ls_command("lsd") is False
    assert is_ls_command("") is False


def test_is_dir_command() -> None:
    assert is_dir_command("dir") is True
    assert is_dir_command("/dir") is False
    assert is_dir_command("  dir  ") is True
    assert is_dir_command("DIR") is True
    assert is_dir_command("directory") is False
    assert is_dir_command("") is False


def test_is_log_command() -> None:
    assert is_log_command("log") is True
    assert is_log_command("/log") is False
    assert is_log_command("  LOG  ") is True
    assert is_log_command("logs") is False


def test_is_last_command() -> None:
    assert is_last_command("last") is True
    assert is_last_command("/last") is False
    assert is_last_command("  LAST  ") is True
    assert is_last_command("lasted") is False


def test_is_whoami_command() -> None:
    assert is_whoami_command("whoami") is True
    assert is_whoami_command("/whoami") is False
    assert is_whoami_command("WHOAMI") is True
    assert is_whoami_command("who am i") is False


def test_parse_blame_command_bare() -> None:
    is_cmd, val = parse_blame_command("blame")
    assert is_cmd is True
    assert val is None


def test_parse_blame_command_with_file() -> None:
    is_cmd, val = parse_blame_command("blame src/main.py")
    assert is_cmd is True
    assert val == "src/main.py"


def test_parse_blame_command_slash_variant_not_matched() -> None:
    is_cmd, _ = parse_blame_command("/blame README.md")
    assert is_cmd is False


def test_parse_blame_command_no_match() -> None:
    is_cmd, _ = parse_blame_command("blaming someone")
    assert is_cmd is False


def test_is_conflicts_command() -> None:
    assert is_conflicts_command("conflicts") is True
    assert is_conflicts_command("/conflicts") is False
    assert is_conflicts_command("CONFLICTS") is True
    assert is_conflicts_command("conflict") is False


def test_bridge_help_text_mentions_new_commands() -> None:
    text = bridge_help_text("auto")
    assert "checkout" in text
    assert "stash" in text
    assert "pull" in text
    assert "ls" in text
    assert "dir" in text
    assert "log" in text
    assert "last" in text
    assert "whoami" in text
    assert "blame" in text
    assert "conflicts" in text


def test_bridge_help_text_omits_removed_commands() -> None:
    text = bridge_help_text("auto")
    assert "remotes" not in text
    assert "tags" not in text
    assert "tree" not in text
    assert "upstream" not in text


def test_simple_commands_reject_embedded_text() -> None:
    for cmd_fn in [
        is_ping_command, is_help_command, is_branch_command,
        is_status_command, is_diff_command, is_pull_command,
        is_ls_command, is_dir_command, is_log_command,
        is_last_command, is_whoami_command, is_conflicts_command,
    ]:
        name = cmd_fn.__name__.replace("is_", "").replace("_command", "")
        assert cmd_fn(f"please run {name}") is False, f"{cmd_fn.__name__} matched mid-sentence"
        assert cmd_fn(f"{name} and then do something") is False, f"{cmd_fn.__name__} matched with trailing text"
        assert cmd_fn(f"can you {name} for me") is False, f"{cmd_fn.__name__} matched embedded"


def test_parse_checkout_rejects_trailing_text() -> None:
    is_cmd, _ = parse_checkout_command("checkout main and fix bug")
    assert is_cmd is False


def test_parse_checkout_rejects_mid_sentence() -> None:
    is_cmd, _ = parse_checkout_command("please checkout main")
    assert is_cmd is False


def test_parse_stash_rejects_trailing_text() -> None:
    is_cmd, _ = parse_stash_command("stash 2 and then apply")
    assert is_cmd is False


def test_parse_stash_rejects_mid_sentence() -> None:
    is_cmd, _ = parse_stash_command("can you stash this")
    assert is_cmd is False


def test_parse_blame_rejects_trailing_text() -> None:
    is_cmd, _ = parse_blame_command("blame src/main.py and fix it")
    assert is_cmd is False


def test_parse_blame_rejects_mid_sentence() -> None:
    is_cmd, _ = parse_blame_command("please blame the file")
    assert is_cmd is False


def test_parse_model_rejects_trailing_text() -> None:
    is_cmd, _ = parse_model_command("model gpt-5 is what I want")
    assert is_cmd is False


def test_parse_model_rejects_mid_sentence() -> None:
    is_cmd, _ = parse_model_command("change model to gpt-5")
    assert is_cmd is False
