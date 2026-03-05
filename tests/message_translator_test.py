from __future__ import annotations

from message_translator import is_stop_command, translate_slack_message


def test_translate_slack_markup() -> None:
    translated = translate_slack_message(
        "<@U123> check <https://example.com|example> in <#C123|general>",
        user_lookup={"U123": "alice"},
    )
    assert translated == "@alice check https://example.com in #general"


def test_translate_without_lookup_falls_back_to_user_id() -> None:
    translated = translate_slack_message("hello <@U999>")
    assert translated == "hello @U999"


def test_smart_quotes_normalized_to_ascii() -> None:
    translated = translate_slack_message('!git commit -am \u201cfix sentry feedback\u201d')
    assert translated == '!git commit -am "fix sentry feedback"'


def test_smart_single_quotes_normalized_to_ascii() -> None:
    translated = translate_slack_message("!git commit -am \u2018fix sentry feedback\u2019")
    assert translated == "!git commit -am 'fix sentry feedback'"


def test_stop_detection() -> None:
    assert is_stop_command("stop")
    assert is_stop_command(" EXIT ")
    assert not is_stop_command("stopping")
